"""Port-specific driver tracing: configuration, mapped Tk UI and real elaborated KDB."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rscheck.checker import check_specs
from rscheck.config import config_from_dict, config_to_dict, load_config, parse_trace_cells, save_config
from rscheck.gui_backend import load_report
from rscheck.inventory import inventory_to_dict, load_inventory
from rscheck.model import ConfigError, ModuleRule, RtlConfig, SpecRow
from rscheck.npi_runner import collect_inventory
from rscheck.reporting import report_to_dict

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config/rscheck.example.json"
RULES = [
    dict(kind=kind, module=module, output=out, inputs=[inp])
    for kind, module, clk, rst in (
        ("DIV", "demo_div", "clk", "rst_n"), ("GATE", "demo_gate", "ci", "ri"),
        ("OCC", "demo_occ", "func_i", "ri"))
    for out, inp in (("co", clk), ("ro", rst))
]
ROW = SpecRow(Path("driver.csv"), "CSV", 2, "data", "demo_rs", "pipe", "driver_top",
              1, "src_clk", "src_rst", "driver_top.src", "NA")
RS = {"demo_rs": ModuleRule(name="demo_rs", has_rs_cfg_en=False, clk_port="cn", rst_port="rn")}


class DriverMapTests(unittest.TestCase):
    def test_configuration_and_validation(self):
        raw = config_to_dict(load_config(CFG))
        raw["rtl"]["trace_cells"] = RULES
        cfg = config_from_dict(raw)
        self.assertEqual(cfg, config_from_dict(config_to_dict(cfg)))
        for cells in ([*RULES, RULES[0]], [dict(RULES[0], inputs=[])], [dict(RULES[0], kind="MUX")],
                      [dict(RULES[0], inputs=["co"])], [dict(RULES[0], inputs=["clk", "clk"])],
                      [RULES[0], dict(RULES[1], kind="OCC")], [dict(RULES[0], module="foo\nbar")]):
            with self.subTest(cells=cells), self.assertRaises(ConfigError):
                parse_trace_cells(cells)
        del raw["rtl"]["trace_cells"]
        self.assertEqual(config_from_dict(raw).rtl.trace_cells, ())

    def test_tk_editor_persists_and_exports(self):
        import tkinter as tk
        from rscheck.gui import RsCheckApp
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(root.destroy)
        app = RsCheckApp(root)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            cfg = load_config(CFG)
            save_config(cfg, path)
            app._apply_config(cfg, path, "test")
            editor = app._open_trace_rule_editor()
            root.update()
            self.assertTrue(editor.winfo_ismapped())
            editor.kind.set("DIV")
            editor.module.set("my_div")
            editor.output.set("clk_o")
            editor.inputs.set("clk_i，test_clk")
            editor.save()
            saved = load_config(path)
            self.assertEqual(saved.module_rules, cfg.module_rules)
            self.assertEqual(next(r.inputs for r in saved.rtl.trace_cells if r.module == "my_div"), ("clk_i", "test_clk"))
            self.assertEqual(app._config_snapshot_from_form().rtl.trace_cells, saved.rtl.trace_cells)
            editor = app._open_trace_rule_editor()
            editor.tree.selection_set(str(next(i for i,r in enumerate(editor.rows) if r.module == "my_div")))
            editor.select()
            editor.remove()
            editor.destroy()  # cancel, keep original on disk
            self.assertEqual(load_config(path), saved)


@unittest.skipUnless(os.environ.get("RSCHECK_DRIVER_KDB") and os.environ.get("RSCHECK_DRIVER_COLLECTOR"),
                     "requires a real Verdi KDB; run scripts/test_vm_driver_map.sh")
class LiveDriverTests(unittest.TestCase):
    def test_gui_edit_then_run_changes_real_crg_result(self):
        import tkinter as tk
        from rscheck.gui import RsCheckApp
        from rscheck.gui_backend import LIVE_SOURCE
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = RsCheckApp(root)
        with tempfile.TemporaryDirectory() as tmp, patch("rscheck.gui.messagebox.showerror") as errors:
            out = Path(tmp)
            cfg = replace(load_config(CFG), module_rules=RS, rtl=RtlConfig(trace_cells=parse_trace_cells(RULES)))
            cfg_path = save_config(cfg, out / "rules.json")
            app._apply_config(cfg, cfg_path, "test")
            sheet = out / "rows.csv"
            sheet.write_text("interface,module,inst,position,step,clk,rst,crg,gate,route,route2\n"
                             "data,demo_rs,pipe,driver_top,1,test_clk,NA,driver_top.decoy,NA,NA,NA\n", encoding="utf-8")
            app.excel_var.set(str(sheet))
            app.collector_var.set(os.environ["RSCHECK_DRIVER_COLLECTOR"])
            app.elab_db_var.set(os.environ["RSCHECK_DRIVER_KDB"])
            app.source_mode_var.set(LIVE_SOURCE)
            app._update_source_mode()
            app.json_report_var.set(str(out / "report.json"))
            app.csv_report_var.set(str(out / "report.csv"))
            app.keep_inventory_var.set("")
            root.update()
            self.assertTrue(root.winfo_ismapped())

            def check():
                app.run_button.invoke()
                deadline = time.monotonic() + 60
                while app.running and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
                self.assertFalse(app.running)
                self.assertFalse(errors.called, str(errors.call_args))
                return load_report(out / "report.json").rows[0]["source_signal_checks"][0]

            self.assertEqual(check()["status"], "not_found")
            editor = app._open_trace_rule_editor()
            root.update()
            self.assertTrue(editor.winfo_ismapped())
            idx = next(i for i, r in enumerate(editor.rows) if (r.module, r.output) == ("demo_occ", "co"))
            editor.tree.selection_set(str(idx))
            editor.select()
            editor.inputs.set("func_i, test_i")
            editor.save()
            result = check()
            self.assertEqual(result["status"], "matched")
            self.assertEqual(result["matching"][0]["path"], ["driver_top.occ_i", "driver_top.decoy"])

    def run_case(self, rows=(ROW,), rules=RULES, depth=4):
        rtl = RtlConfig(trace_cells=parse_trace_cells(rules), crg_trace_max_depth=depth)
        inv = collect_inventory(os.environ["RSCHECK_DRIVER_COLLECTOR"], rows, rtl, RS,
                                elab_db=os.environ["RSCHECK_DRIVER_KDB"], timeout_seconds=60)
        rep = check_specs(rows, inv, rtl, RS)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "saved.json"
            path.write_text(json.dumps(inventory_to_dict(inv)), encoding="utf-8")
            self.assertEqual(load_inventory(path), inv)
            path.write_text(json.dumps(report_to_dict(rep)), encoding="utf-8")
            load_report(path)  # same evidence validator as interactive GUI
        return inv, rep

    def test_reaches_both_roles_at_limit_and_does_not_follow_controls(self):
        decoy = replace(ROW, row_number=3, crg_source="driver_top.decoy", clk="test_clk", rst="enable")
        near = replace(ROW, row_number=4, crg_source="driver_top.gate_i", clk="gc", rst="gr")
        inv, rep = self.run_case((ROW, decoy, near))
        ok, miss, early = rep.rows
        self.assertEqual([s.status for s in ok.source_signals], ["matched", "matched"])
        for s in ok.source_signals:
            self.assertEqual(s.matching[0].depth, 4)
            self.assertEqual(s.matching[0].path, tuple("driver_top."+p for p in ("occ_i","gate_i","div_i","src")))
            self.assertTrue(any("DIV driver_top.div_i" in d for d in s.diagnostics))
        self.assertEqual([s.status for s in miss.source_signals], ["not_found", "not_found"])
        self.assertEqual([s.matching[0].depth for s in early.source_signals], [2, 2])
        self.assertFalse(any(n.instance=="driver_top.div_i" for n in early.instances[0].clock_trace.modules))
        limited = check_specs([ROW], inv, RtlConfig(), RS).rows[0]
        self.assertEqual([s.status for s in limited.source_signals], ["unavailable", "unavailable"])

    def test_narrow_depth_no_rules_unmapped_output_and_invalid_input(self):
        _, rep = self.run_case(depth=3)
        self.assertEqual([s.status for s in rep.rows[0].source_signals], ["depth_limited"]*2)
        _, rep = self.run_case(rules=[])
        self.assertEqual([s.status for s in rep.rows[0].source_signals], ["not_found"]*2)
        _, rep = self.run_case(rules=[r for r in RULES if not (r["module"]=="demo_gate" and r["output"]=="ro")])
        self.assertEqual([s.status for s in rep.rows[0].source_signals], ["matched","not_found"])
        for wrong in ("missing", "ro"):
            rules = [dict(r, inputs=[wrong]) if r["module"]=="demo_gate" and r["output"]=="co" else r for r in RULES]
            _, rep = self.run_case(rules=rules)
            checks = rep.rows[0].source_signals
            self.assertEqual([s.status for s in checks], ["unavailable","matched"])
            self.assertTrue(any("missing or not an input" in d for d in checks[0].diagnostics))

    def test_only_explicit_alternate_input_opens_decoy_branch(self):
        decoy = replace(ROW, crg_source="driver_top.decoy", clk="test_clk", rst="NA")
        rules = [dict(r, inputs=["func_i", "test_i"]) if r["module"]=="demo_occ" and r["output"]=="co" else r for r in RULES]
        _, rep = self.run_case((decoy,), rules)
        self.assertEqual(rep.rows[0].source_signals[0].matching[0].depth, 2)
