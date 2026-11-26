from __future__ import annotations

import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rscheck.checker import check_specs
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui import _evidence_payload, _instance_display, _review_sections
from rscheck.gui_backend import GuiReportError, load_report
from rscheck.inventory import load_inventory
from rscheck.model import PositionInventory, RS_INST_FUZZY_RULE, RS_INST_GROUP_RULE
from rscheck.reporting import format_console_report, report_to_dict, write_csv_report, write_json_report


ROOT = Path(__file__).resolve().parents[1]


class ExplicitNameFallbackTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config/rscheck.example.json")
        self.inv = load_inventory(ROOT / "examples/inventory.json")
        self.group, self.control = read_spec_rows(
            ROOT / "examples/specs.csv", self.config.excel,
            self.config.position_mappings, self.config.crg_source_mappings,
        )
        self.typo = replace(self.control, rs_inst="CTRL_RS_DO", rs_route="AAAA_BBB_C2")
        self.pos = self.control.position
        self.target = next(i for i in self.inv.positions[self.pos].instances if i.name == "CTRL_RS_D0")

    def check(self, *rows, inventory=None):
        return check_specs(rows, self.inv if inventory is None else inventory,
                           self.config.rtl, self.config.module_rules)

    def test_unique_fallback_round_trip_and_user_visible_diagnostics(self):
        report = self.check(self.typo)
        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].instances, (self.target,))
        self.assertEqual(report.warning_count, 0)
        with tempfile.TemporaryDirectory() as tmp:
            record, = load_report(write_json_report(report, Path(tmp) / "result.json")).rows
            with write_csv_report(report, Path(tmp) / "result.csv").open(encoding="utf-8-sig") as stream:
                entry, = csv.DictReader(stream)
        match = record["instance_match"]
        self.assertEqual((match["rule"], match["matched_count"], match["candidate_count"],
                          match["best_distance"], match["tied_best_count"]),
                         (RS_INST_FUZZY_RULE, 1, 7, 1, 1))
        self.assertEqual(match["selected"], {"name": self.target.name, "full_name": self.target.full_name,
                                             "distance": 1})
        self.assertEqual(match, json.loads(entry["instance_match"]))
        self.assertEqual((record["spec"]["RS_inst"], entry["RS_inst"], entry["RS_route"]),
                         ("CTRL_RS_DO", "CTRL_RS_DO", "AAAA_BBB_C2"))
        self.assertIn("CTRL_RS_DO → CTRL_RS_D0", _instance_display(record["spec"], record))
        payload = _evidence_payload(record)
        self.assertIn("唯一最近实例", payload["例化匹配说明"])
        self.assertIn("RS_route 不参与", payload["例化匹配说明"])
        self.assertIn("例化匹配 · RS_inst 模糊回退", dict(_review_sections(record, payload)))
        console = format_console_report(report)
        self.assertIn("RS_inst fuzzy fallback: matched; query='CTRL_RS_DO'", console)
        self.assertIn("selected=top.u_tile.CTRL_RS_D0", console)

    def test_ordinary_groups_and_exact_wrong_module_are_not_overridden(self):
        record = report_to_dict(self.check(self.group))["rows"][0]
        self.assertEqual((record["instance_match"]["rule"], len(record["matched_instances"]),
                          record["step_check"]["effective_step"]), (RS_INST_GROUP_RULE, 6, 5))
        bad = replace(self.target, module="other_module", name=self.typo.rs_inst,
                      full_name=self.pos + "." + self.typo.rs_inst)
        with_bad = replace(self.inv, positions={self.pos: PositionInventory(True, (bad, self.target))})
        row, = self.check(self.typo, inventory=with_bad).rows
        self.assertEqual(row.instances, (bad,))
        self.assertIsNone(row.nearest_match)
        self.assertIn("RS_MODULE_MISMATCH", [f.code for f in row.findings])

    def test_candidate_scope_single_selection_and_downstream_checks(self):
        # Wrong module / wrong parent are closer, and a legal suffix of the winner
        # also exists. None may be adopted along with (or instead of) the winner.
        wrong = replace(self.target, module="other", name="CTRL_RS_DX", full_name=self.pos+".CTRL_RS_DX")
        peer = replace(self.target, name="CTRL_RS_O", full_name="top.other.CTRL_RS_O")
        extra = replace(self.target, name=self.target.name+"_C1", full_name=self.target.full_name+"_C1")
        inv = replace(self.inv, positions={self.pos: PositionInventory(True, (extra, wrong, self.target)),
                                           "top.other": PositionInventory(True, (peer,))})
        row, = self.check(replace(self.typo, rs_inst="CTRL_RS_O", step=0, clk="wrong"),
                          inventory=inv).rows
        self.assertEqual(row.instances, (self.target,))
        self.assertEqual((row.nearest_match.candidate_count, row.effective_step), (2, 1))
        self.assertEqual({f.code for f in row.findings}, {"STEP_MISMATCH", "CLK_CONNECTION_MISMATCH"})
        # A typo and a directly matched name still cannot own the same instance.
        overlap, = self.check(self.typo, replace(self.control, row_number=77)).global_findings
        self.assertEqual((overlap.code, overlap.instance), ("AMBIGUOUS_GROUP_MATCH", self.target.full_name))
        zero, = self.check(replace(self.group, rs_inst="AAAA_BBB_X2", step=0, rs_route="")).rows
        self.assertTrue(zero.passed)
        self.assertEqual((zero.instances[0].name, zero.effective_step), ("AAAA_BBB_C2", 0))

    def test_tie_without_route_does_not_steal_from_another_position(self):
        tie = replace(self.typo, rs_inst="AAAA_BBB_X", rs_route="NA")
        rev = replace(self.inv, positions={self.pos: PositionInventory(
            True, tuple(reversed(self.inv.positions[self.pos].instances)))})
        for inv in (self.inv, rev):
            report = self.check(tie, inventory=inv)
            record = report_to_dict(report)["rows"][0]
            self.assertFalse(report.passed)
            self.assertEqual([f["code"] for f in record["findings"]], ["RS_INST_AMBIGUOUS"])
            match = record["instance_match"]
            self.assertEqual((match["status"], match["best_distance"], match["tied_best_count"]),
                             ("ambiguous", 2, 6))
            self.assertEqual([i["name"] for i in match["top_candidates"]], ["AAAA_BBB_C"+str(i) for i in range(5)])
            self.assertIsNone(match["selected"])
            self.assertEqual(record["matched_instances"], [])
        missing, = self.check(replace(self.typo, position="top.missing")).rows
        self.assertEqual([f.code for f in missing.findings], ["POSITION_NOT_FOUND"])
        self.assertIsNone(missing.nearest_match)

    def test_bad_fuzzy_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            for key, value in (("selected", {}), ("mode", "RS_route"), ("candidate_count", 0)):
                data = report_to_dict(self.check(self.typo))
                data["rows"][0]["instance_match"][key] = value
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(key=key), self.assertRaises(GuiReportError):
                    load_report(path)
