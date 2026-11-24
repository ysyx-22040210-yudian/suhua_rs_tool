"""Mapped Tk end-to-end check. Set RSCHECK_ROUTE_COLLECTOR/KDB for live NPI."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import INVENTORY_SOURCE, LIVE_SOURCE


ROOT = Path(__file__).resolve().parents[1]


class RouteGuiTests(unittest.TestCase):
    def test_route_result_and_evidence_in_mapped_window(self):
        collector = os.environ.get("RSCHECK_ROUTE_COLLECTOR")
        kdb = os.environ.get("RSCHECK_ROUTE_KDB")
        self.assertEqual(bool(kdb), bool(collector), "set both live NPI inputs")
        if tk is None:
            if kdb:
                self.fail("Tk is required for live GUI verification")
            self.skipTest("Python has no Tk bindings")
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            if kdb:
                raise
            self.skipTest(f"Tk display unavailable: {exc}")
        app = RsCheckApp(root)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch("rscheck.gui.messagebox.showerror") as error:
                out = Path(tmp)
                app.json_report_var.set(str(out / "result.json"))
                app.csv_report_var.set(str(out / "result.csv"))
                app.keep_inventory_var.set("")
                if kdb:
                    app.source_mode_var.set(LIVE_SOURCE)
                    app.collector_var.set(collector)
                    app.elab_db_var.set(kdb)
                    app.npi_lib_var.set(os.environ.get("RSCHECK_ROUTE_NPI_LIB", ""))
                else:
                    app.source_mode_var.set(INVENTORY_SOURCE)
                    app.inventory_var.set(str(ROOT / "examples" / "inventory.json"))
                app._update_source_mode()
                root.update()
                self.assertTrue(root.winfo_ismapped())
                self.assertEqual(app.column_vars["RS_route"].get(), "10")
                sample = ROOT / "examples" / "specs_route.csv"
                tie = out / "tie.csv"
                lines = sample.read_text(encoding="utf-8").splitlines()
                tie.write_text(lines[0] + "\n" + lines[1].replace("CTRL_RS_DO", "AAAA_BBB_C") + "\n", encoding="utf-8")

                for case, state in ((sample, "PASS"), (tie, "FAIL")):
                    app.excel_var.set(str(case))
                    app.run_button.invoke()
                    deadline = time.monotonic() + 120
                    while app.running and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.02)
                    self.assertFalse(app.running, "GUI timed out")
                    self.assertFalse(error.called, str(error.call_args))
                    self.assertEqual(app.summary_state_var.get(), state, app.log_text.get("1.0", "end")[-2000:])
                    app.result_tree.selection_set("row-0")
                    app._on_result_selected()
                    root.update()
                    display = str(app.result_tree.set("row-0", "rs_inst"))
                    evidence = json.loads(app.evidence_text.get("1.0", "end"))
                    match = evidence["instance_match"]
                    self.assertEqual(match["candidate_count"], 7)
                    if state == "PASS":
                        self.assertIn("CTRL_RS_D0", display)
                        self.assertIn("唯一最近实例", evidence["例化匹配说明"])
                        self.assertEqual(match["selected"]["full_name"], "top.u_tile.CTRL_RS_D0")
                        self.assertEqual(match["best_distance"], 1)
                        self.assertEqual(app._result_records["row-1"]["step_check"]["effective_step"], 0)
                    else:
                        self.assertIn("并列", display)
                        self.assertIsNone(match["selected"])
                        self.assertEqual(match["tied_best_count"], 6)
                        self.assertEqual(len(match["top_candidates"]), 5)
                    self.assertFalse(app.controller.is_running)
        finally:
            if app.controller.is_running:
                app.controller.cancel()
            root.destroy()
