from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import LIVE_SOURCE
from rscheck.hierarchy_cache import HierarchyCache
from rscheck.hierarchy_tree import ScopeTree
from rscheck.npi_runner import HierarchyIndex, HierarchyScope, collect_hierarchy, search_hierarchy


def scope(path, name=None, kind="module"):
    return HierarchyScope(path, name if name is not None else path.rsplit(".", 1)[-1], "block", kind)


def index(scopes):
    return HierarchyIndex(tuple(sorted(scopes, key=lambda item: item.path)), (), (), False, len(scopes))


def payload(scopes):
    return {"schema": "rscheck.hierarchy", "schema_version": 1,
            "scopes": [s.as_dict() for s in index(scopes).scopes],
            "visited_scopes": len(scopes), "warnings": [], "notices": [], "truncated": False}


class ScopeTreeTests(unittest.TestCase):
    def test_multiple_roots_generate_escaped_names_and_missing_containers(self):
        escaped = r"top.\u.part"
        dut = ScopeTree.from_index(index([
            scope("tb"), scope("top"), scope("top.gen[4]", kind="gen_scope"),
            scope("top.gen[4].u"), scope("top.hidden.u"), scope("top.hidden2.missing.leaf"),
            scope(escaped, r"\u.part"), scope(escaped + " .u"),
            scope("top.r"), scope("top.r.name", "r.name"), scope("top2.u")]))
        self.assertEqual(dut.children[""], ["tb", "top", "top2.u"])
        self.assertEqual(dut.parents["top.r.name"], "top")  # one leaf, not a child of r
        self.assertEqual(dut.parents["top.hidden2.missing.leaf"], "top")
        self.assertEqual(dut.parents[escaped + " .u"], escaped)
        self.assertEqual(dut.filtered_children(["top.gen[4].u", escaped]),
                         {"": ["top"], "top": [escaped, "top.gen[4]"],
                          "top.gen[4]": ["top.gen[4].u"]})
        self.assertEqual(dut.filtered_children([]), {})

    def test_cli_private_indices_do_not_mix_kdbs(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            exe = root / "collector"
            exe.touch()
            cache = root / "one.json"
            a, b = root / "a.elab++", root / "b.elab++"
            a.mkdir()
            b.mkdir()
            def run(cmd, **kwargs):
                top = "A" if str(a) in cmd else "B"
                Path(cmd[cmd.index("--output")+1]).write_text(json.dumps(
                    payload([scope(top), scope(top + ".crg")])), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
            with patch("rscheck.npi_runner.subprocess.run", side_effect=run) as collector:
                collect_hierarchy(exe, elab_db=a, index_output=cache)
                for query, count in [(r"^A\.", 1), ("(?i)CRG$", 1), ("absent", 0)]:
                    actual = search_hierarchy(exe, query, elab_db=a, cached_index=cache)
                    self.assertEqual(actual.total_matches, count)
                self.assertEqual(collector.call_count, 1)
                second = search_hierarchy(exe, "^A", elab_db=b, cached_index=root/"two.json")
                self.assertEqual(second.total_matches, 0)
                self.assertEqual(collector.call_count, 2)

    def test_mapped_tk_tree_paging_regex_and_editor_isolation(self):
        if tk is None:
            self.skipTest("Tk unavailable")
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no Tk display: {exc}")
        self.addCleanup(root.destroy)
        app = RsCheckApp(root)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        work = Path(temp.name)
        app._hierarchy_cache = HierarchyCache(work / "cache")
        self.addCleanup(app._release_hierarchy_session)
        root.update()
        source = [scope("top")] + [scope(f"top.u_{i:03}") for i in range(620)]
        source += [scope("top.u_619.crg")]
        document = json.dumps(payload(source))
        app.position_path_var.set("keep.position")
        app.crg_source_path_var.set("keep.crg")
        app.crg_source_alias_var.set("my_crg")
        mapping = dict(app._crg_source_mappings)
        app.source_mode_var.set(LIVE_SOURCE)
        app.collector_var.set("fixture-collector")
        db = work / "test.elab++"
        db.mkdir()
        app.elab_db_var.set(str(db))
        app._update_source_mode()
        app.notebook.select(app.crg_sources_tab)
        from rscheck.gui_backend import ProcessResult
        controller_run = app.controller.run
        calls = []
        def run(cmd, **kwargs):
            calls.append("browse" if "--browse" in cmd else "filter")
            if "--browse" not in cmd:
                return controller_run(cmd, **kwargs)  # real CLI/regex against snapshot
            Path(cmd[cmd.index("--session-index")+1]).write_text(document, encoding="utf-8")
            return ProcessResult(tuple(cmd), 0, "", "")
        def wait():
            until = time.monotonic() + 12
            while app.running and time.monotonic() < until:
                root.update()
                time.sleep(.01)
            root.update()
            self.assertFalse(app.running, "DUT picker stalled")
        with patch.object(app.controller, "run", side_effect=run), patch("rscheck.gui.messagebox.showerror") as error:
            app.crg_source_hierarchy_pick_button.invoke()
            wait()
            self.assertTrue(app._position_hierarchy_dialog.winfo_ismapped())
            session = Path(app._hierarchy_temp.name)
            tree = app._position_hierarchy_tree
            top = app._scope_iid("top")
            self.assertEqual(tree.get_children(), (top,))
            tree.focus(top)
            tree.event_generate("<<TreeviewOpen>>")
            tree.item(top, open=True)
            self.assertEqual(len(tree.get_children(top)), 201)  # 200 real nodes + next page
            for _ in range(3):
                tree.selection_set("more:top")
                app._on_hierarchy_return(None)
            self.assertEqual(len(tree.get_children(top)), 620)
            for query, expected in [("^top", 500), ("(?i)CRG$", 1), ("no_such_scope", 0)]:
                app.position_hierarchy_regex_var.set(query)
                app.position_hierarchy_search_button.invoke()
                wait()
                self.assertEqual(len(app._hierarchy_hits), expected)
                if expected == 500:
                    self.assertIn("622", app.position_hierarchy_status_var.get())
                    self.assertIn("截断", app.position_hierarchy_status_var.get())
                if expected == 1:
                    leaf = app._scope_iid("top.u_619.crg")
                    self.assertEqual(tree.get_children(top), (tree.parent(leaf),))
                    self.assertEqual(tree.selection(), (leaf,))
                if not expected:
                    self.assertEqual(tree.get_children(), ())
                    self.assertTrue(app.position_hierarchy_use_button.instate(["disabled"]))
            app.position_hierarchy_regex_var.set("[")
            app.position_hierarchy_search_button.invoke()
            error.assert_called_once()
            app.hierarchy_clear_button.invoke()
            self.assertEqual(tree.get_children(), (top,))
            self.assertEqual(calls, ["browse", "filter", "filter", "filter"])
            self.assertEqual(app.crg_source_path_var.get(), "keep.crg")
            tree.selection_set(top)
            app._on_position_hierarchy_candidate_selected()
            app.position_hierarchy_use_button.invoke()
            self.assertTrue((session / "index.json").is_file())
            self.assertEqual(app.crg_source_path_var.get(), "top")
            self.assertEqual(app.position_path_var.get(), "keep.position")
            self.assertEqual(app.crg_source_alias_var.get(), "my_crg")
            self.assertEqual(app._crg_source_mappings, mapping)
            self.assertFalse(app._crg_source_mappings_dirty)
            for button in (app.position_hierarchy_pick_button, app.crg_source_hierarchy_pick_button):
                button.invoke()
                wait()
                self.assertIn("命中内存缓存", app.position_hierarchy_status_var.get())
                self.assertEqual(app._position_hierarchy_tree.get_children(), (top,))
                self.assertEqual(app.position_hierarchy_regex_var.get(), "")
                app.position_hierarchy_close_button.invoke()
            self.assertEqual(calls.count("browse"), 1)
            app.position_hierarchy_pick_button.invoke()
            wait()
            app.hierarchy_cache_clear_button.invoke()
            wait()
            self.assertEqual(calls.count("browse"), 2)
            self.assertIn("已从 KDB 加载", app.position_hierarchy_status_var.get())
            app.position_hierarchy_close_button.invoke()
            app._release_hierarchy_session()
            self.assertFalse(session.exists())  # persistent file remains for a new GUI
            app.crg_source_hierarchy_pick_button.invoke()
            wait()
            self.assertIn("命中本地缓存", app.position_hierarchy_status_var.get())
            self.assertEqual(calls.count("browse"), 2)
            app.position_hierarchy_close_button.invoke()
