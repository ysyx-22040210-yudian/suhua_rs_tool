from __future__ import annotations

import csv
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rscheck.checker import check_specs
from rscheck.cli import main
from rscheck.config import config_from_dict, config_to_dict, load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui import _evidence_payload, _instance_display
from rscheck.gui_backend import GuiReportError, GuiRunRequest, build_validate_command, load_report
from rscheck.inventory import load_inventory
from rscheck.model import PositionInventory, WorkbookError
from rscheck.reporting import format_console_report, report_to_dict, write_csv_report, write_json_report


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "rscheck.example.json"
SAMPLE = ROOT / "examples" / "specs_route.csv"


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG)
        self.rows = read_spec_rows(SAMPLE, self.config.excel, self.config.position_mappings,
                                   self.config.crg_source_mappings)
        self.row = self.rows[0]
        self.inv = load_inventory(ROOT / "examples" / "inventory.json")

    def check(self, *specs, inventory=None):
        return check_specs(specs, inventory or self.inv, self.config.rtl, self.config.module_rules)

    def test_unique_typo_and_exact_zero_step_with_full_evidence(self):
        result = self.check(*self.rows)
        self.assertTrue(result.passed)
        self.assertEqual(result.warning_count, 0)
        self.assertEqual([len(row.instances) for row in result.rows], [1, 1])
        self.assertEqual([row.effective_step for row in result.rows], [1, 0])
        with tempfile.TemporaryDirectory() as tmp:
            loaded = load_report(write_json_report(result, Path(tmp) / "report.json"))
            with write_csv_report(result, Path(tmp) / "report.csv").open(encoding="utf-8-sig") as f:
                csv_rows = list(csv.DictReader(f))
        for n, (expected_name, distance) in enumerate([("CTRL_RS_D0", 1), ("AAAA_BBB_C2", 0)]):
            record = loaded.rows[n]
            evidence = record["instance_match"]
            self.assertEqual(evidence["selected"], {"name": expected_name, "distance": distance,
                             "full_name": "top.u_tile." + expected_name})
            self.assertEqual((evidence["candidate_count"], evidence["tied_best_count"]), (7, 1))
            self.assertEqual(record["spec"]["RS_inst"], "")
            self.assertEqual(csv_rows[n]["RS_route"], self.rows[n].rs_route)
            self.assertEqual(json.loads(csv_rows[n]["instance_match"]), evidence)
            self.assertIn(expected_name, _instance_display(record["spec"], record))
            self.assertIn("唯一最近实例", _evidence_payload(record)["例化匹配说明"])
        self.assertIn("selected=top.u_tile.CTRL_RS_D0", format_console_report(result))

    def test_second_best_ties_do_not_matter_and_tied_first_is_not_selected(self):
        spec = replace(self.row, rs_route="AAAA_BBB_C")
        for inv in (self.inv, replace(self.inv, positions={self.row.position:
                    replace(self.inv.positions[self.row.position], instances=tuple(
                        reversed(self.inv.positions[self.row.position].instances)))})):
            row = self.check(spec, inventory=inv).rows[0]
            self.assertEqual([f.code for f in row.findings], ["RS_ROUTE_AMBIGUOUS"])
            evidence = row.route_match.as_dict()
            self.assertEqual((evidence["best_distance"], evidence["tied_best_count"]), (1, 6))
            self.assertEqual([item["name"] for item in evidence["top_candidates"]],
                             ["AAAA_BBB_C" + str(i) for i in range(5)])
            self.assertEqual(row.instances, ())
            self.assertIsNone(evidence["selected"])

    def test_only_current_parent_and_exact_module_are_candidates(self):
        pos = self.row.position
        base = self.inv.positions[pos]
        original = next(i for i in base.instances if i.name == "CTRL_RS_D0")
        wrong_module = replace(original, module="RS_PIPE", name=self.row.rs_route,
                               full_name=pos + "." + self.row.rs_route)
        other_pos = "top.other"
        other = replace(wrong_module, module=self.row.rs_module, full_name=other_pos + "." + wrong_module.name)
        inv = replace(self.inv, positions={
            pos: replace(base, instances=(wrong_module, original)),
            other_pos: PositionInventory(True, (other,)),
        })
        row = self.check(self.row, inventory=inv).rows[0]
        self.assertEqual(row.instances, (original,))
        self.assertEqual(row.route_match.candidate_count, 1)
        # Literals and case matter. No regex, normalization or minimum score cut-off.
        for query, distance in (("CTRL_RS_D.", 1), ("ctrl_rs_d0", 7), ("CTRL_D0", 3), ("CTRL_RS_D0X", 1)):
            match = self.check(replace(self.row, rs_route=query), inventory=inv).rows[0]
            self.assertEqual(match.route_match.ranked[0].distance, distance)
            self.assertEqual(match.instances, (original,))

    def test_duplicate_leaf_names_in_generate_scopes_are_ambiguous(self):
        src = self.inv.positions[self.row.position].instances[0]
        siblings = tuple(replace(src, full_name=f"{self.row.position}.gen[{i}].{src.name}") for i in (1,0))
        inv = replace(self.inv, positions={self.row.position: PositionInventory(True, siblings)})
        row = self.check(replace(self.row, rs_module=src.module, rs_route=src.name), inventory=inv).rows[0]
        self.assertEqual(row.route_match.status, "ambiguous")
        self.assertEqual([r.full_name for r in row.route_match.ranked], sorted(i.full_name for i in siblings))

    def test_no_candidates_and_no_position_are_distinct(self):
        for spec, code, status in [
            (replace(self.row, rs_module="missing_module"), "RS_ROUTE_NO_CANDIDATES", "no_candidates"),
            (replace(self.row, position="missing.position"), "POSITION_NOT_FOUND", "position_not_found"),
        ]:
            row = self.check(spec).rows[0]
            self.assertFalse(row.passed)
            self.assertEqual([f.code for f in row.findings], [code])
            self.assertEqual((row.route_match.status, row.route_match.candidate_count), (status, 0))

    def test_closest_leaf_does_not_expand_as_a_prefix(self):
        pos = self.row.position
        src = next(i for i in self.inv.positions[pos].instances if i.name == "CTRL_RS_D0")
        child = replace(src, name=src.name + "_C1", full_name=src.full_name + "_C1")
        inv = replace(self.inv, positions={pos: PositionInventory(True, (child, src))})
        self.assertEqual(self.check(self.row, inventory=inv).rows[0].instances, (src,))

    def test_xlsx_blank_selector_uses_mapped_reference(self):
        # Column K in the distributed sheet is empty; J3 contains the typo.
        excel = replace(self.config.excel, data_start_row=3,
                        columns=dict(self.config.excel.columns, RS_inst=11))
        parsed = read_spec_rows(ROOT / "examples" / "RS_Check_Excel_Template.xlsx", excel,
                                self.config.position_mappings, self.config.crg_source_mappings)
        row, = self.check(*parsed).rows
        self.assertTrue(row.passed)
        self.assertEqual((row.spec.rs_inst, row.spec.rs_route), ("", "CTRL_RS_DO"))
        self.assertEqual(row.route_match.ranked[0].distance, 1)

    def test_route_diagnostics_must_agree_with_selected_instance_on_report_reload(self):
        data = report_to_dict(self.check(self.row))
        data["rows"][0]["instance_match"]["selected"]["full_name"] = "wrong"
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "bad.json"
            f.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "selected instance"):
                load_report(f)

    def test_explicit_inst_precedence_including_unsuccessful_explicit_selector(self):
        original = read_spec_rows(ROOT / "examples" / "specs.csv", self.config.excel,
                                  self.config.position_mappings, self.config.crg_source_mappings)[0]
        result = self.check(replace(original, rs_route="CTRL_RS_D0"))
        self.assertTrue(result.passed)
        self.assertEqual(len(result.rows[0].instances), 6)
        match = report_to_dict(result)["rows"][0]["instance_match"]
        self.assertEqual((match["mode"], match["route_ignored"]), ("RS_inst", True))
        failure = self.check(replace(self.row, rs_inst="absent"))
        self.assertFalse(failure.passed)
        self.assertEqual(failure.rows[0].findings[0].code, "GROUP_NOT_FOUND")

    def test_selected_one_still_checks_ports_step_and_duplicate_ownership(self):
        wrong = self.check(replace(self.row, step=0, clk="wrong"))
        self.assertTrue({"STEP_MISMATCH", "CLK_CONNECTION_MISMATCH"} <= {f.code for f in wrong.rows[0].findings})
        for spec in (replace(self.row, rs_inst="CTRL_RS_D0"), replace(self.row, rs_route="CTRL_RS_D0")):
            result = self.check(self.row, replace(spec, row_number=4))
            overlap, = result.global_findings
            self.assertEqual(overlap.code, "AMBIGUOUS_GROUP_MATCH")
            self.assertIn("row 4", overlap.message)
            self.assertIn("RS_route=CTRL_RS_DO", overlap.actual)

    def test_old_config_ignores_tenth_column_and_disabling_route_is_explicit(self):
        raw = config_to_dict(self.config)
        del raw["columns"]["RS_route"]
        old = config_from_dict(raw)
        with self.assertRaisesRegex(WorkbookError, "RS_inst or RS_route"):
            read_spec_rows(SAMPLE, old.excel)
        rows = read_spec_rows(ROOT / "examples" / "specs.csv", old.excel)
        self.assertEqual([s.rs_route for s in rows], ["", ""])
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "old.json"
            legacy.write_text(json.dumps(raw), encoding="utf-8")
            for file, columns, specfile in [(legacy, {**old.excel.columns, "RS_route": 10}, SAMPLE),
                                          (CONFIG, old.excel.columns, ROOT / "examples" / "specs.csv")]:
                request = GuiRunRequest(excel_path=str(specfile), config_path=str(file),
                                        columns={k: str(v) for k,v in columns.items()})
                cmd = build_validate_command(request)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(cmd[3:]), 0)
                data = json.loads(out.getvalue())
                self.assertEqual(data[0]["RS_route"], "CTRL_RS_DO" if file == legacy else "")

    def test_reordered_route_column_and_duplicate_after_alias_resolution(self):
        columns = dict(self.config.excel.columns, RS_route=13)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "reordered.csv"
            lines = SAMPLE.read_text(encoding="utf-8").splitlines()
            def moved(line):
                fields = line.split(",")
                return ",".join(fields[:9] + ["unmapped", "备注", "more", fields[9]])
            lines = [moved(s) for s in lines]
            f.write_text("\n".join(lines), encoding="utf-8")
            def parse():
                return read_spec_rows(f, replace(self.config.excel, columns=columns), self.config.position_mappings)
            self.assertEqual([r.rs_route for r in parse()], [r.rs_route for r in self.rows])
            f.write_text("\n".join(lines + [lines[1].replace("tile_core", "top.u_tile")]), encoding="utf-8")
            with self.assertRaisesRegex(WorkbookError, r"duplicate group .*RS_route=CTRL_RS_DO"):
                parse()
