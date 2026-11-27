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
from rscheck.config import load_config, save_config
from rscheck.gui_backend import GuiReportError, load_report
from rscheck.inventory import inventory_to_dict, load_inventory
from rscheck.match_review import format_match_details
from rscheck.model import ActualInstance, Inventory, InventoryError, ModuleRule, PositionInventory, RtlConfig, SpecRow
from rscheck.npi_runner import collect_inventory
from rscheck.position_bounds import SUBTREE_SCOPE, enclosing_boundary
from rscheck.reporting import report_to_dict

ROOT = Path(__file__).resolve().parents[1]
TOP, SUB = "boundary_top", "boundary_top.tile"
DEEP, PEER = SUB + ".lane[0].wrap_deep", TOP + ".tile2"
P, L = "PIPE_C0", "LEFT_RS"
RS = {"bound_rs": ModuleRule(name="bound_rs", has_rs_cfg_en=False, clk_port="clk", rst_port="rst_n")}


def row(pos, name, number, step=1):
    return SpecRow(Path("spec.csv"), "CSV", number, "data", "bound_rs", name, pos, step, "NA", "NA", "NA", "NA")


def inst(pos, name):
    return ActualInstance(name, pos + "." + name, "bound_rs", "", None, {}, {}, ())


def fixture(modern=True):
    return Inventory({
        TOP: PositionInventory(True, (inst(TOP, P), inst(PEER, P)), (SUB,)),
        SUB: PositionInventory(True, (inst(SUB, P),), (DEEP,)),
        DEEP: PositionInventory(True, (inst(DEEP, P), inst(DEEP, L))),
    }, schema_version=4, position_scope=SUBTREE_SCOPE if modern else None)


def check(rows, inv=None):
    return check_specs(rows, fixture() if inv is None else inv, RtlConfig(), RS)


class PositionBoundaryTests(unittest.TestCase):
    def test_postorder_residuals_same_leaf_and_workbook_order(self):
        parent, child, grand = row(TOP, "PIPE", 2, 2), row(SUB, "PIPE", 3), row(DEEP, P, 4)
        leftover = row(TOP, L, 5)
        for specs in ((parent, child, grand, leftover), (grand, leftover, parent, child)):
            report = check(specs)
            self.assertEqual(report.error_count, 0)
            self.assertEqual([r.spec.row_number for r in report.rows], [s.row_number for s in specs])
            paths = {r.spec.row_number: {i.full_name for i in r.instances} for r in report.rows}
            self.assertEqual(paths, {2:{TOP+"."+P, PEER+"."+P}, 3:{SUB+"."+P}, 4:{DEEP+"."+P}, 5:{DEEP+"."+L}})
            matched = next(r for r in report_to_dict(report)["rows"] if r["spec"]["row"] == 2)
            proof = matched["instance_match"]["boundary_ownership"]
            self.assertEqual(proof["excluded_count"], 2)
            self.assertEqual({(e["owner"], *e["rows"]) for e in proof["excluded_sample"]}, {(SUB,3),(DEEP,4)})
            self.assertIn(DEEP+"."+P, format_match_details(matched))

    def test_sparse_workbook_missing_selector_and_failed_step(self):
        # Replay a saved multi-boundary inventory with only the top row active.
        self.assertEqual(len(check([row(TOP, P, 2, 4)]).rows[0].instances), 4)
        rep = check([row(TOP, P, 2, 2), row(SUB, P, 3, 0), row(DEEP, "NA", 4)])
        self.assertEqual([len(r.instances) for r in rep.rows], [2, 2, 0])
        self.assertIn("STEP_MISMATCH", [f.code for f in rep.rows[1].findings])
        self.assertEqual(rep.rows[0].boundary_excluded, 2)
        # Same-depth conflicts stay errors; they are not resolved by first-row order.
        rep = check([row(TOP, P, 2, 3), row(DEEP, P, 3), row(DEEP, P, 4)])
        self.assertEqual([f.code for f in rep.global_findings], ["AMBIGUOUS_GROUP_MATCH"])
        self.assertEqual(rep.rows[0].boundary_excluded, 1)

    def test_child_fuzzy_claim_precedes_parent_fuzzy_ranking_and_partial_lists(self):
        inv = fixture()
        inv = replace(inv, positions={
            TOP: PositionInventory(True, (inst(TOP, "PIPE_X"),), (SUB,)),
            SUB: PositionInventory(True, (inst(SUB, "PIPE_Y"), inst(SUB, "BUS_A"), inst(SUB, "BUS_B"))),
        })
        rep = check([row(TOP, "PIPE_Z", 2), row(SUB, "PIPE_YY;BUS_?", 3)], inv)
        parent, child = rep.rows
        self.assertEqual([i.name for i in child.instances], ["PIPE_Y"])
        self.assertFalse(child.passed)
        self.assertEqual(parent.nearest_match.candidate_count, 3)
        self.assertEqual([i.full_name for i in parent.instances], [TOP+".PIPE_X"])

    def test_route_runs_on_the_residual_pool_not_the_original_ties(self):
        inv = replace(fixture(), positions={
            TOP: PositionInventory(True, (inst(TOP,"DATA_A"), inst(TOP,"DATA_B")), (SUB,)),
            SUB: PositionInventory(True, (inst(SUB,"DATA_A"),)),
        })
        spec = replace(row(TOP, "DATA_X", 2), rs_route="DATA_A", rs_route_mapped=True)
        rep = check([spec, row(SUB,"DATA_A",3)], inv)
        self.assertEqual(rep.rows[0].route_field_used, "RS_route")
        self.assertEqual([i.full_name for i in rep.rows[0].instances], [TOP+".DATA_A"])
        self.assertEqual(rep.rows[0].nearest_match.candidate_count, 2)

    def test_legacy_snapshot_component_boundaries_and_duplicate_physical_paths(self):
        parent, child = row(TOP, P, 2, 2), row(SUB, P, 3, 2)
        inv = fixture(False)
        # Legacy / manually made inventories can list one instance in two buckets.
        inv = replace(inv, positions={**inv.positions, TOP:PositionInventory(True, (*inv.positions[TOP].instances, inst(SUB,P)))})
        rep = check([parent,child], inv)
        self.assertEqual(rep.error_count, 0)
        self.assertEqual(rep.rows[0].boundary_excluded, 2)
        self.assertIn("POSITION_INVENTORY_LEGACY", [f.code for f in rep.global_findings])
        self.assertIsNone(enclosing_boundary(PEER, {SUB}))
        self.assertEqual(enclosing_boundary(r"top.\u.a .rs", {"top",r"top.\u",r"top.\u.a"}),r"top.\u.a")

    def test_corrupt_boundary_graph_rejected_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as name:
            f = Path(name)/"inventory.json"
            doc = inventory_to_dict(fixture())
            f.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(load_inventory(f), fixture())
            for invalid in ([TOP], [SUB,SUB], ["absent"]):
                doc["positions"][DEEP]["child_positions"] = invalid
                f.write_text(json.dumps(doc), encoding="utf-8")
                with self.assertRaises(InventoryError):
                    load_inventory(f)
            doc = inventory_to_dict(fixture())
            doc["positions"][TOP]["instances"].append(doc["positions"][DEEP]["instances"][0])
            f.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "repeated across position"):
                load_inventory(f)

    def test_saved_report_checks_ownership_sample(self):
        report = report_to_dict(check([row(TOP,P,2,3), row(DEEP,P,3)]))
        with tempfile.TemporaryDirectory() as name:
            f = Path(name)/"report.json"
            f.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual(len(load_report(f).rows), 2)
            report["rows"][0]["instance_match"]["boundary_ownership"]["excluded_sample"][0]["rows"] = []
            f.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "child instance ownership"):
                load_report(f)


@unittest.skipUnless(os.environ.get("RSCHECK_BOUNDARY_COLLECTOR") and os.environ.get("RSCHECK_BOUNDARY_KDB"),
                     "requires real nested KDB; run scripts/test_vm_position_bounds.sh")
class LiveBoundaryTests(unittest.TestCase):
    def test_partition_and_cross_boundary_crg(self):
        specs = (replace(row(TOP, L, 2), clk="root_clk", crg_source=TOP+".src"),
                 row(SUB, P, 3), row(DEEP, P, 4), row(TOP, P, 5, 2))
        inv = collect_inventory(os.environ["RSCHECK_BOUNDARY_COLLECTOR"], specs, RtlConfig(), RS,
                                elab_db=os.environ["RSCHECK_BOUNDARY_KDB"], timeout_seconds=60)
        self.assertEqual(inv.position_scope, SUBTREE_SCOPE)
        self.assertEqual(inv.positions[TOP].child_positions, (SUB,))
        self.assertEqual(inv.positions[SUB].child_positions, (DEEP,))
        physical = [i.full_name for b in inv.positions.values() for i in b.instances]
        self.assertEqual(len(physical), len(set(physical)))
        rep = check(specs, inv)
        self.assertEqual((rep.error_count, rep.warning_count), (0,0))
        match = rep.rows[0]
        self.assertEqual(match.instances[0].full_name, DEEP+"."+L)
        self.assertEqual(match.source_signals[0].matching[0].connection, TOP+".root_clk")
        with tempfile.TemporaryDirectory() as name:
            f = Path(name)/"report.json"
            f.write_text(json.dumps(report_to_dict(rep)), encoding="utf-8")
            self.assertEqual(len(load_report(f).rows), 4)

    def test_gui_aliases_parent_first_and_skipped_boundary(self):
        import tkinter as tk
        from rscheck.gui import RsCheckApp
        from rscheck.gui_backend import LIVE_SOURCE
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = RsCheckApp(root)
        with tempfile.TemporaryDirectory() as tmp, patch("rscheck.gui.messagebox.showerror") as errors:
            d = Path(tmp)
            cfg = replace(load_config(ROOT/"config/rscheck.example.json"), module_rules=RS,
                          position_mappings={"root":TOP, "child":SUB, "deep":DEEP, "peer":PEER})
            app._apply_config(cfg, save_config(cfg, d/"config.json"), "test")
            inp = d/"rows.csv"
            inp.write_text("interface,module,inst,boundary,step,clk,rst,crg,gate,route,route2\n"
                           "data,bound_rs,PIPE,root,2,NA,NA,NA,NA,NA,NA\n"
                           "data,bound_rs,PIPE,child,1,NA,NA,NA,NA,NA,NA\n"
                           "data,bound_rs,PIPE_C0,deep,1,NA,NA,NA,NA,NA,NA\n"
                           "data,bound_rs,LEFT_RS,root,1,NA,NA,NA,NA,NA,NA\n"
                           "NA,bound_rs,PIPE,peer,1,NA,NA,NA,NA,NA,NA\n", encoding="utf-8")
            app.excel_var.set(str(inp))
            app.json_report_var.set(str(d/"r.json"))
            app.csv_report_var.set(str(d/"r.csv"))
            app.keep_inventory_var.set(str(d/"i.json"))
            app.collector_var.set(os.environ["RSCHECK_BOUNDARY_COLLECTOR"])
            app.elab_db_var.set(os.environ["RSCHECK_BOUNDARY_KDB"])
            app.source_mode_var.set(LIVE_SOURCE)
            app._update_source_mode()
            root.update()
            self.assertTrue(root.winfo_ismapped())
            app.run_button.invoke()
            until = time.monotonic()+60
            while app.running and time.monotonic()<until:
                root.update(); time.sleep(.02)
            self.assertFalse(app.running)
            self.assertFalse(errors.called, str(errors.call_args))
            report = load_report(d/"r.json")
            self.assertEqual(report.summary["errors"], 0)
            self.assertEqual(report.summary["skipped_rows"], 1)
            self.assertEqual([r["spec"]["row"] for r in report.rows], [2,3,4,5])
            self.assertIn(DEEP+"."+P, format_match_details(report.rows[0]))
            self.assertNotIn(PEER, load_inventory(d/"i.json").positions)
