"""Presentation contracts against real Tk widgets; no Verdi or new packages."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rscheck.checker import check_specs
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import INVENTORY_SOURCE, LIVE_SOURCE, LoadedReport
from rscheck.inventory import load_inventory
from rscheck.reporting import report_to_dict


ROOT = Path(__file__).resolve().parents[1]


class StudioTests(unittest.TestCase):
    def setUp(self):
        if tk is None:
            self.skipTest("Python has no Tk")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.addCleanup(self.close_window)
        self.app = RsCheckApp(self.root)
        self.root.geometry("980x680")
        self.root.update()

    def close_window(self):
        # These tests own the event loop; do not leave timer callbacks behind.
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()

    def test_named_font_lookup_uses_its_own_tk_interpreter_on_python38(self):
        # Create a name only in the second interpreter. Looking it up via the
        # default root, or via the post-3.8 nametofont(root=...) API, must fail.
        from tkinter import font
        from rscheck.gui_style import named_font

        second = tk.Tk()
        self.addCleanup(second.destroy)
        second.withdraw()
        name = "RsCheckOnlyInSecondRoot"
        self.assertNotIn(name, font.names(self.root))
        original = font.Font(root=second, name=name, family="Helvetica", size=19)
        existing = named_font(second, name)
        self.assertEqual(existing.cget("size"), 19)
        existing.configure(size=17)
        self.assertEqual(original.cget("size"), 17)

    def assert_in_window(self, widget):
        self.assertTrue(widget.winfo_ismapped())
        x = widget.winfo_rootx() - self.root.winfo_rootx()
        y = widget.winfo_rooty() - self.root.winfo_rooty()
        self.assertGreaterEqual(min(x, y), 0)
        self.assertLessEqual(x + widget.winfo_width(), self.root.winfo_width())
        self.assertLessEqual(y + widget.winfo_height(), self.root.winfo_height())

    def report(self, mixed=False, source=ROOT / "examples" / "specs_route.csv"):
        cfg = load_config(ROOT / "config" / "rscheck.example.json")
        specs = read_spec_rows(source, cfg.excel, cfg.position_mappings, cfg.crg_source_mappings)
        if mixed:
            specs[1] = replace(specs[1], rs_route="AAAA_BBB_C")
        live = check_specs(specs, load_inventory(ROOT / "examples" / "inventory.json"),
                           cfg.rtl, cfg.module_rules)
        raw = report_to_dict(live)
        if mixed:
            raw["global_findings"].append({"code": "NPI_LOAD_PARTIAL", "severity": "warning", "message": "test"})
            raw["summary"]["warnings"] += 1
        return LoadedReport(raw["summary"], tuple(raw["global_findings"]), tuple(raw["rows"]), raw)

    def test_six_pages_keep_primary_actions_reachable_at_minimum_size(self):
        app = self.app
        self.assertEqual(app.notebook.select(), str(app.setup_tab))
        self.assert_in_window(app.config_export_button)
        for page, _icon, _title, _label, _group, _hint in app._pages:
            app.nav_buttons[str(page)].invoke()
            self.root.update()
            self.assertEqual(app.notebook.select(), str(page))
            self.assertEqual(app.nav_buttons[str(page)].cget("style"), "Selected.Nav.TButton")
            self.assert_in_window(app.run_button)
            self.assert_in_window(app.validate_button)
            self.assert_in_window(app.cancel_button)
            self.assert_in_window(app.nav_buttons[str(page)])
            if page == app.rules_tab:
                tree = app.rule_tree
                widths = sum(int(tree.column(c, "width")) for c in tree["columns"])
                self.assertLessEqual(widths, tree.winfo_width() + 2)

    def test_paper_theme_renders_curves_not_square_borders(self):
        from rscheck import gui_style as design

        theme = self.app.ui
        for component in ("Surface.TFrame", "Metric.TFrame", "TButton", "Primary.TButton",
                          "Selected.Nav.TButton", "Segment.TRadiobutton", "TEntry", "TSpinbox",
                          "TNotebook.Tab", "Live.Badge.TLabel"):
            with self.subTest(component=component):
                self.assertTrue(theme.style.layout(component)[0][0].startswith("RsPaper."))
        radius = theme.rounded.pixels(12)
        image = theme.rounded.photo(radius, design.PAPER, design.ACTION, design.ACTION)
        rgb = lambda value: tuple(bytes.fromhex(value[1:]))
        self.assertEqual(image.get(0, 0), rgb(design.PAPER))
        self.assertEqual(image.get(radius, radius), rgb(design.ACTION))
        # Curve edge has antialias coverage; resizing reuses a non-degenerate
        # centre tile (one-pixel tiles cripple the Tk 8.6 Windows renderer).
        self.assertGreater(len({image.get(x, y) for x in range(radius) for y in range(radius)}), 2)
        self.assertGreaterEqual(image.width() - 2 * radius, 32)
        self.assertIs(image, theme.rounded.photo(radius, design.PAPER, design.ACTION, design.ACTION))
        self.assertNotEqual(design.ACTION, design.SUCCESS)

    def test_rounded_controls_keep_native_arrow_keyboard_and_disabled_actions(self):
        from tkinter import ttk

        top = tk.Toplevel(self.root)
        value = tk.IntVar(master=top, value=3)
        spin = ttk.Spinbox(top, from_=1, to=9, textvariable=value)
        spin.pack()
        calls = []
        button = ttk.Button(top, text="运行", style="Primary.TButton", command=lambda: calls.append(1))
        button.pack()
        self.root.update()
        up = next(((x, y) for x in range(spin.winfo_width()-1, spin.winfo_width()//2, -1)
                   for y in range(1, spin.winfo_height()) if spin.identify(x, y).endswith("uparrow")), None)
        self.assertIsNotNone(up, "skin must expose the native up arrow's hit area")
        x, y = up
        spin.event_generate("<ButtonPress-1>", x=x, y=y)
        spin.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.assertEqual(value.get(), 4)
        spin.focus_force()
        self.root.update()
        spin.event_generate("<KeyPress-Down>")
        self.assertEqual(value.get(), 3)
        self.assertEqual(spin.tk_focusNext(), button)
        button.focus_force()
        self.root.update()
        button.event_generate("<KeyPress-space>")
        self.root.update()
        self.assertEqual(calls, [1])
        button.state(["disabled"])
        button.invoke()
        self.assertEqual(calls, [1])

    def test_source_disclosure_does_not_discard_values_and_busy_state_restores(self):
        a = self.app
        a.npi_timeout_var.set("93")
        a.advanced_options_var.set(True)
        self.root.update()
        self.assertTrue(a.live_advanced.winfo_ismapped())
        a.setup_form.canvas.yview_moveto(1.0)
        self.assert_in_window(a.run_button)
        a.source_mode_var.set(INVENTORY_SOURCE)
        a._update_source_mode()
        self.root.update()
        self.assertFalse(a.live_advanced.winfo_ismapped())
        self.assertIn("离线", a.mode_badge.cget("text"))
        self.assertEqual(a.npi_timeout_var.get(), "93")
        a.source_mode_var.set(LIVE_SOURCE)
        a._update_source_mode()
        a._set_running(True)
        self.root.update()
        self.assertTrue(a.run_button.instate(["disabled"]))
        self.assertFalse(a.cancel_button.instate(["disabled"]))
        self.assertTrue(a.config_import_button.instate(["disabled"]))
        self.assertTrue(a.progress.winfo_ismapped())
        a._set_running(False)
        self.root.update()
        self.assertFalse(a.run_button.instate(["disabled"]))
        self.assertFalse(a.config_import_button.instate(["disabled"]))
        self.assertTrue(a.cancel_button.instate(["disabled"]))
        self.assertFalse(a.progress.winfo_ismapped())

    def test_issue_filter_includes_global_warnings_clears_stale_evidence_and_survives_rerun(self):
        a = self.app
        a.notebook.select(a.results_tab)
        a._render_report(self.report(mixed=True))
        a.only_issues_var.set(True)
        a._filter_results()
        self.assertEqual(a.result_tree.get_children(), ("global", "row-1"))
        a.result_tree.selection_set("row-1")
        a._on_result_selected()
        self.root.update()
        self.assertIn("并列", a.review_text.get("1.0", "end"))
        payload = json.loads(a.evidence_text.get("1.0", "end"))
        self.assertEqual(payload["instance_match"]["tied_best_count"], 6)
        self.assertEqual(a.metric_values[3].cget("text"), "1")
        a._render_report(self.report())  # Reuses IDs of previously detached items.
        self.root.update()
        self.assertEqual(a.result_tree.get_children(), ())
        self.assertTrue(a.result_empty.winfo_ismapped())
        self.assertFalse(a.finding_tree.get_children())
        self.assertEqual(a.evidence_text.get("1.0", "end-1c"), "")
        self.assertEqual(a.metric_values[1].cget("text"), "2")  # counters reflect the complete report
        a.only_issues_var.set(False)
        a._filter_results()
        self.root.update()
        self.assertEqual(a.result_tree.get_children(), ("row-0", "row-1"))
        self.assertIn("top.u_tile.CTRL_RS_D0", a.review_text.get("1.0", "end"))
        self.assertFalse(a.result_empty.winfo_ismapped())

    def test_validation_inspector_does_not_claim_rtl_checks_were_performed(self):
        a = self.app
        a.notebook.select(a.results_tab)
        sample = tuple(row["spec"] for row in self.report().rows)
        a._render_validation(sample)
        self.root.update()
        self.assertIn("仅验证规格表", a.review_text.get("1.0", "end"))
        self.assertEqual(a.summary_state_var.get(), "VALID")
        self.assertIsNone(json.loads(a.evidence_text.get("1.0", "end"))["instance_match"])

    def test_route_colons_survive_from_spec_to_table_summary_and_json(self):
        a = self.app
        a.notebook.select(a.results_tab)
        sample = (ROOT / "examples" / "specs_route.csv").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            sheet = Path(tmp) / "routes.csv"
            for query in ("CTRL:RS_D0", "CTRL：RS_D0", "CTRL::RS_D0"):
                with self.subTest(query=query):
                    sheet.write_text(sample.replace("CTRL_RS_DO", query), encoding="utf-8")
                    a._render_report(self.report(source=sheet))
                    self.root.update()
                    self.assertIn(query, a.result_tree.set("row-0", "rs_inst"))
                    self.assertIn(query, a.review_text.get("1.0", "end"))
                    match = json.loads(a.evidence_text.get("1.0", "end"))["instance_match"]
                    self.assertEqual(match["query"], query)
                    self.assertEqual(match["selected"]["name"], "CTRL_RS_D0")
