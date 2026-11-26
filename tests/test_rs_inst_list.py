from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rscheck.checker import check_specs
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui import _evidence_payload, _instance_display
from rscheck.gui_backend import GuiReportError, load_report, load_validation_rows
from rscheck.inventory import load_inventory
from rscheck.match_review import format_match_details
from rscheck.model import (
    PositionInventory, RS_INST_FUZZY_RULE, RS_INST_GROUP_RULE, RS_INST_LEGACY_SPLIT_RULE,
    RS_INST_LIST_RULE, RS_INST_SPLIT_RULE, WorkbookError, split_rs_inst,
)
from rscheck.reporting import format_console_report, report_to_dict, write_csv_report, write_json_report
from tests.test_excel_reader import _xlsx


ROOT = Path(__file__).resolve().parents[1]


class InstanceListTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config/rscheck.example.json")
        self.inv = load_inventory(ROOT / "examples/inventory.json")
        self.base, self.ctrl = self.read(ROOT / "examples/specs.csv")

    def read(self, path):
        return read_spec_rows(path, self.config.excel, self.config.position_mappings,
                              self.config.crg_source_mappings)

    def check(self, *rows, inventory=None, rtl=None):
        return check_specs(rows, inventory or self.inv, rtl or self.config.rtl, self.config.module_rules)

    def test_token_boundaries_order_colons_and_escaped_names(self):
        for separator in (" ", "   ", "\t", "\u3000", "\u00a0", ",", "，", "、", ":", "::", "：",
                          "\n", "\r", "\r\n", "\n\n", ";", "；", " \t，::； \r\n、： "):
            with self.subTest(separator=separator):
                raw = "； A" + separator + "B" + separator + "gen[0].u ;"
                self.assertEqual(split_rs_inst(raw), ("A", "B", "gen[0].u"))
        self.assertEqual(split_rs_inst(r"\bus;A,1 ; B"), (r"\bus;A,1", "B"))
        self.assertEqual(split_rs_inst(r"A,\bus::1、2,3 ：B"), ("A", r"\bus::1、2,3", "B"))
        self.assertEqual(split_rs_inst(r"A\nB"), (r"A\nB",))
        for internal in ("/", "|", "-", "_", ".", "[0]"):
            with self.subTest(internal=internal):
                name = "A" + internal + "B"
                self.assertEqual(split_rs_inst("  " + name + "\t "), (name,))
                self.assertEqual(split_rs_inst(name + "；C" + internal + "D"), (name, "C" + internal + "D"))
        self.assertEqual(split_rs_inst("  A  B； \tC，D  \n E、F  "), ("A", "B", "C", "D", "E", "F"))
        self.assertEqual(split_rs_inst(" ;； 、，, ::：\t\n "), ())
        self.assertEqual(split_rs_inst(r"\bus;A,1  suffix;Z"), (r"\bus;A,1", "suffix", "Z"))
        self.assertEqual(split_rs_inst("prefix \\bus;A,1\\  \nZ"), ("prefix", "\\bus;A,1\\", "Z"))

    def test_line_continuation_is_not_a_selector_or_part_of_an_escaped_name(self):
        for br in ("\n", "\r\n", "\r"):
            for joiner in ("；\\", ";\\", "； \\\t", " \\", "\\", "：\\", "、\\  ", ",\t\\"):
                with self.subTest(br=br, joiner=joiner):
                    raw = "AAAA_BBB_C3" + joiner + br + "  AAAA_BBB_C4"
                    self.assertEqual(split_rs_inst(raw), ("AAAA_BBB_C3", "AAAA_BBB_C4"))
        self.assertEqual(split_rs_inst("A；\\ \n\n\t\\\r\nB;\\" + "\nC"), ("A", "B", "C"))
        self.assertEqual(split_rs_inst("\\u;flag\\\n\\x,y "), ("\\u;flag\\", "\\x,y"))
        self.assertEqual(split_rs_inst("A；\\\n\\bus[0] "), ("A", "\\bus[0]"))
        for same_line in (r"A；\B", r"A；\nB", "A；\\"):
            self.assertEqual(split_rs_inst(same_line), ("A", same_line[2:]))

    def test_cached_continuation_keeps_raw_evidence_and_fuzzy_matches_two_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "continued.xlsx"
            raw = "AAAA_BBB_X3；\\  \r\n  AAAA_BBB_X4"
            _xlsx(path, cell_overrides={
                "C2": '<c r="C2" t="str"><f>K2</f><v>' + raw.replace("\r", "_x000D_") + '</v></c>',
                "J2": '<c r="J2" t="inlineStr"><is><t>CTRL_RS_D0</t></is></c>',
            })
            spec, = self.read(path)
            self.assertEqual(spec.rs_inst, raw)
            report = self.check(spec)
            self.assertTrue(report.passed)
            record, = load_report(write_json_report(report, Path(tmp) / "out.json")).rows
            match = record["instance_match"]
            self.assertTrue(match["route_ignored"])
            self.assertEqual((match["query"], match["matched_count"], record["step_check"]["effective_step"]),
                             (raw, 2, 2))
            self.assertEqual([(i["query"], i["rule"], i["selected"]["name"]) for i in match["items"]], [
                ("AAAA_BBB_X3", RS_INST_FUZZY_RULE, "AAAA_BBB_C3"), ("AAAA_BBB_X4", RS_INST_FUZZY_RULE, "AAAA_BBB_C4"),
            ])

    def test_only_continuations_never_override_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last-break.xlsx"
            raw = "；\\ \n\t"
            cell = f'<c r="C2" t="inlineStr"><is><t xml:space="preserve">{raw}</t></is></c>'
            _xlsx(path, cell_overrides={"C2": cell, "J2": '<c r="J2" t="inlineStr"><is><t>CTRL_RS_DO</t></is></c>'})
            spec, = self.read(path)
            self.assertEqual((spec.rs_inst, spec.rs_inst_items), (raw, ()))
            match = report_to_dict(self.check(spec))["rows"][0]["instance_match"]
            self.assertEqual((match["mode"], match["query"]), ("none", ""))
            _xlsx(path, cell_overrides={"C2": cell})
            no_selection, = self.read(path)
            self.assertEqual(report_to_dict(self.check(no_selection))["rows"][0]["instance_match"]["mode"], "none")

    def test_real_xlsx_union_in_gui_json_and_csv(self):
        sample = self.read(ROOT / "examples/specs_multi_inst.xlsx")
        input_rows = load_validation_rows(json.dumps([s.as_dict() for s in sample]))
        self.assertEqual(input_rows[0]["RS_inst"], "AAAA_BBB_C0  AAAA_BBB_X2")
        self.assertEqual(input_rows[0]["RS_inst_items"], ["AAAA_BBB_C0", "AAAA_BBB_X2"])
        self.assertIn("AAAA_BBB_C0；AAAA_BBB_X2", _instance_display(input_rows[0], {}))
        report = self.check(*sample)
        self.assertTrue(report.passed)
        self.assertEqual([r.effective_step for r in report.rows], [1, 2, 1])
        self.assertEqual([len(r.instances) for r in report.rows], [2, 2, 1])
        with tempfile.TemporaryDirectory() as tmp:
            records = load_report(write_json_report(report, Path(tmp) / "check.json")).rows
            with write_csv_report(report, Path(tmp) / "check.csv").open(encoding="utf-8-sig") as stream:
                csv_rows = list(csv.DictReader(stream))
        for record, csv_row in zip(records, csv_rows):
            match = record["instance_match"]
            self.assertEqual(match["rule"], RS_INST_LIST_RULE)
            self.assertEqual(csv_row["RS_inst"], record["spec"]["RS_inst"])
            self.assertEqual(match, json.loads(csv_row["instance_match"]))
        exact, fuzzy = records[0]["instance_match"]["items"]
        self.assertEqual((exact["rule"], exact["matched_paths"]), (RS_INST_GROUP_RULE, ["top.u_tile.AAAA_BBB_C0"]))
        self.assertEqual((fuzzy["query"], fuzzy["selected"]["name"], fuzzy["best_distance"]),
                         ("AAAA_BBB_X2", "AAAA_BBB_C2", 1))
        self.assertIn("重叠 1 个", _evidence_payload(records[2])["例化匹配说明"])
        self.assertIn("newly_counted=0", format_console_report(report))

    def test_each_item_uses_the_same_candidates_as_if_entered_alone(self):
        # In this inventory C1 contributes 1 and C2 contributes 0. Never remove a
        # previously selected instance from the candidate pool or retry a tied item using RS_route.
        cases = (
            ("AAAA_BBB_C1；AAAA_BBB_X2", ("AAAA_BBB_C1", "AAAA_BBB_C2"), (1, 1), 1),
            ("AAAA_BBB_X2   AAAA_BBB_C1", ("AAAA_BBB_C2", "AAAA_BBB_C1"), (1, 1), 1),
            ("AAAA_BBB_X1：AAAA_BBB_X2", ("AAAA_BBB_C1", "AAAA_BBB_C2"), (1, 1), 1),
            ("AAAA_BBB_X2、AAAA_BBX_C2\tAAAA_BBB_X2", ("AAAA_BBB_C2",) * 3, (1, 0, 0), 0),
            ("AAAA_BBB_X,AAAA_BBB_X2", (None, "AAAA_BBB_C2"), (0, 1), None),
        )
        for raw, winners, added, step in cases:
            with self.subTest(raw=raw):
                spec = replace(self.base, rs_inst=raw, rs_route="CTRL_RS_D0", step=1 if step is None else step)
                row, = self.check(spec).rows
                self.assertEqual((row.passed, row.effective_step), (step is not None, step))
                self.assertEqual(tuple(item.query for item in row.item_matches), spec.rs_inst_items)
                self.assertEqual(tuple(item.added_count for item in row.item_matches), added)
                for item, winner in zip(row.item_matches, winners):
                    solo, = self.check(replace(spec, rs_inst=item.query, rs_route="NA")).rows
                    self.assertEqual(item.paths, tuple(i.full_name for i in solo.instances))
                    self.assertEqual(item.paths, (f"top.u_tile.{winner}",) if winner else ())
                    self.assertEqual(item.nearest, solo.nearest_match)
                    if item.query == winner:
                        self.assertEqual(item.rule, RS_INST_GROUP_RULE)
                    else:
                        self.assertEqual(item.rule, RS_INST_FUZZY_RULE)
                        self.assertEqual((item.nearest.candidate_count, item.nearest.tied_best_count),
                                         (7, 1 if winner else 6))

    def test_mixed_separators_preserve_per_item_exact_fuzzy_and_ignored_route(self):
        for separator in ("  ", "\t", ",", "，", "、", ":", "：", "::", "\t、，::；\r\n"):
            with self.subTest(separator=separator):
                spec = replace(self.base, rs_inst="AAAA_BBB_C3" + separator + "AAAA_BBB_X4",
                               rs_route="CTRL_RS_D0", step=2)
                report = self.check(spec)
                self.assertTrue(report.passed)
                record = report_to_dict(report)["rows"][0]
                match = record["instance_match"]
                self.assertTrue(match["route_ignored"])
                self.assertEqual(record["step_check"]["effective_step"], 2)
                self.assertEqual([(i["query"], i["rule"], i["matched_paths"]) for i in match["items"]], [
                    ("AAAA_BBB_C3", RS_INST_GROUP_RULE, ["top.u_tile.AAAA_BBB_C3"]),
                    ("AAAA_BBB_X4", RS_INST_FUZZY_RULE, ["top.u_tile.AAAA_BBB_C4"]),
                ])

    def test_multiline_workbook_keeps_three_spec_rows_and_one_total_per_row(self):
        rows = self.read(ROOT / "examples/specs_multiline_inst.xlsx")
        self.assertEqual([s.row_number for s in rows], [2, 3, 4])
        self.assertEqual([s.rs_inst_items for s in rows], [
            ("AAAA_BBB_C0", "AAAA_BBB_X2"), ("AAAA_BBB_C3", "AAAA_BBB_C4"), ("CTRL_RS_D0", "CTRL_RS_DO"),
        ])
        self.assertIn("\n\n", rows[1].rs_inst)
        report = self.check(*rows)
        self.assertTrue(report.passed)
        self.assertEqual([row.effective_step for row in report.rows], [1, 2, 1])
        self.assertEqual([[f.actual["field"] for f in row.findings] for row in report.rows], [["RS_route"]]*3)
        summaries = [line for line in format_console_report(report).splitlines() if line.startswith("[WARNING] row")]
        self.assertEqual(len(summaries), 3)
        self.assertTrue(all(" effective=" in line for line in summaries))
        with tempfile.TemporaryDirectory() as tmp:
            records = load_report(write_json_report(report, Path(tmp) / "result.json")).rows
            with write_csv_report(report, Path(tmp) / "result.csv").open(encoding="utf-8-sig", newline="") as f:
                csv_rows = list(csv.DictReader(f))
            self.assertEqual([r["RS_inst"] for r in csv_rows], [s.rs_inst for s in rows])
            self.assertEqual([r["spec"]["RS_inst"] for r in records], [s.rs_inst for s in rows])
            self.assertEqual([item["added_count"] for item in records[2]["instance_match"]["items"]], [1, 0])

    def test_xlsx_break_encodings_and_protected_literals(self):
        # Real Excel saves a pasted CRLF as _x000D_ followed by LF, and a CHAR(13)
        # formula result as _x000D_. A literal marker is protected in both storage types.
        xml = "A_x000D_\nB_x000d_C_x000A_D_x005F_x000D_E;F_x005f_x000A_G_x0009_H_x005F_x0009_I"
        raw = "A\r\nB\rC\nD_x000D_E;F_x000A_G\tH_x0009_I"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "breaks.xlsx"
            for kind, payload in (("inlineStr", f"<is><t>{xml}</t></is>"), ("s", "<v>0</v>"),
                                  ("str", f"<f>K2</f><v>{xml}</v>")):
                with self.subTest(kind=kind):
                    _xlsx(path, cell_overrides={"C2": f'<c r="C2" t="{kind}">{payload}</c>'})
                    if kind == "s":
                        with zipfile.ZipFile(path, "a") as archive:
                            archive.writestr("xl/sharedStrings.xml", f"<sst><si><r><t>{xml}</t></r></si></sst>")
                    row, = self.read(path)
                    self.assertEqual(row.rs_inst, raw)
                    self.assertEqual(row.rs_inst_items, ("A", "B", "C", "D_x000D_E", "F_x000A_G", "H_x0009_I"))
            # Markup boundaries must not manufacture an escape from two independent rich-text runs.
            _xlsx(path, cell_overrides={"C2": '<c r="C2" t="inlineStr"><is><r><t>U_x00</t></r>'
                                             '<r><t>0D_V</t></r></is></c>'})
            self.assertEqual(self.read(path)[0].rs_inst_items, ("U_x000D_V",))

    def test_overlap_does_not_duplicate_ports_parameters_step_or_ownership(self):
        spec = replace(self.base, rs_inst="AAAA_BBB；AAAA_BBB_C2; AAAA_BBB_X2\nAAAA_BBB", rs_route="CTRL_RS_D0")
        row, = self.check(spec).rows
        self.assertTrue(row.passed)
        self.assertEqual([item.added_count for item in row.item_matches], [6, 0, 0, 0])
        self.assertEqual((len(row.instances), len(row.step_evaluations), len(row.crg_evaluations),
                          row.effective_step), (6, 6, 6, 5))
        other = replace(self.base, row_number=9, rs_inst="AAAA_BBB_C2", step=0)
        conflict, = self.check(spec, other).global_findings
        self.assertEqual((conflict.code, conflict.instance), ("AMBIGUOUS_GROUP_MATCH", "top.u_tile.AAAA_BBB_C2"))
        self.assertEqual([f.code for f in self.check(replace(spec, step=9)).rows[0].findings], ["STEP_MISMATCH"])

    def test_independent_group_index_sequences(self):
        base = self.inv.positions[self.base.position].instances[0]
        instances = tuple(replace(base, name=n, full_name=self.base.position+"."+n)
                          for n in ("LEFT_C0", "RIGHT_D0", "LEFT_C1", "RIGHT_D1"))
        inv = replace(self.inv, positions={self.base.position: PositionInventory(True, instances)})
        row, = self.check(replace(self.base, rs_inst="LEFT; RIGHT", step=4), inventory=inv,
                          rtl=replace(self.config.rtl, require_contiguous_indices=True)).rows
        self.assertTrue(row.passed, row.findings)
        self.assertFalse(row.findings)

    def test_partial_match_survives_as_evidence_not_as_total(self):
        spec = replace(self.base, rs_inst="AAAA_BBB_C0；AAAA_BBB_X", step=1, rs_route="CTRL_RS_D0")
        report = self.check(spec)
        row, = report.rows
        self.assertFalse(row.passed)
        self.assertIsNone(row.effective_step)
        self.assertEqual([f.code for f in row.findings], ["RS_INST_AMBIGUOUS"])
        self.assertEqual([i.name for i in row.instances], ["AAAA_BBB_C0"])
        with tempfile.TemporaryDirectory() as tmp:
            record, = load_report(write_json_report(report, Path(tmp) / "partial.json")).rows
        match = record["instance_match"]
        self.assertTrue(match["route_ignored"])
        self.assertEqual(match["items"][1]["tied_best_count"], 6)
        self.assertIn("1 项未选中", _instance_display(record["spec"], record))
        self.assertIn("并列", _evidence_payload(record)["例化匹配说明"])
        for bad, status in [(replace(spec, position="top.absent"), "position_not_found"),
                            (replace(spec, rs_module="absent", rs_inst="X;Y", rs_route="NA"), "no_candidates")]:
            data = report_to_dict(self.check(bad))["rows"][0]
            self.assertFalse(data["passed"])
            self.assertEqual([i["status"] for i in data["instance_match"]["items"]], [status, status])

    def test_formula_cache_and_separator_only_follow_same_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "listed.xlsx"
            def read_cell(text, route):
                _xlsx(path, cell_overrides={
                    "C2": f'<c r="C2" t="str"><f>K2</f><v>{text}</v></c>',
                    "J2": f'<c r="J2" t="inlineStr"><is><t>{route}</t></is></c>',
                })
                return self.read(path)[0]
            row = read_cell("A；B  C，：D&#9;E", "r1::x；r2")
            self.assertEqual(row.rs_inst_items, ("A", "B", "C", "D", "E"))
            row = read_cell("\n；_x000D_ ;，、::&#9;\n", "r1::x；r2")
            self.assertEqual(row.rs_inst_items, ())
            match = report_to_dict(self.check(row))["rows"][0]["instance_match"]
            self.assertEqual((match["mode"], match["query"]), ("none", ""))
            self.assertIsNone(self.check(read_cell("、 ,，::：&#9;", "")).rows[0].effective_step)
            row = read_cell("： 、CTRL_RS_DO，", "")
            with tempfile.TemporaryDirectory() as out:
                record, = load_report(write_json_report(self.check(row), Path(out) / "single.json")).rows
                self.assertEqual(record["instance_match"]["query"], "CTRL_RS_DO")

    def test_formula_result_is_the_selector_at_the_users_mapped_column(self):
        cfg = replace(self.config.excel, columns=dict(self.config.excel.columns, RS_inst=27))
        cases = (
            ('"AAAA_BBB_C"&amp;3', "AAAA_BBB_C3", 1, ("AAAA_BBB_C3",), ("AAAA_BBB_C3",)),
            ('"AAAA_BBB_C3"&amp;"；"&amp;CHAR(10)&amp;"AAAA_BBB_X4"', "AAAA_BBB_C3；\nAAAA_BBB_X4", 2,
             ("AAAA_BBB_C3", "AAAA_BBB_X4"), ("AAAA_BBB_C3", "AAAA_BBB_C4")),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapped.xlsm"
            for expression, cached, step, tokens, chosen in cases:
                with self.subTest(expression=expression):
                    _xlsx(path, cell_overrides={
                        "AA2": f'<c r="AA2" t="str"><f>{expression}</f><v>{cached}</v></c>',
                        "C2": '<c r="C2" t="e"><f>NA()</f><v>#N/A</v></c>',  # unmapped
                        "E2": f'<c r="E2"><v>{step}</v></c>',
                        "J2": '<c r="J2" t="str"><v>CTRL_RS_DO</v></c>',  # must not win over a formula
                    })
                    spec, = read_spec_rows(path, cfg, self.config.position_mappings, self.config.crg_source_mappings)
                    self.assertEqual((spec.rs_inst, spec.rs_inst_items), (cached, tokens))
                    record, = load_report(write_json_report(self.check(spec), Path(tmp)/"r.json")).rows
                    self.assertEqual(record["spec"]["RS_inst_formula_cell"], "AA2")
                    self.assertEqual(record["instance_match"]["mode"], "RS_inst")
                    self.assertTrue(record["passed"])
                    self.assertEqual(tuple(i["name"] for i in record["matched_instances"]), chosen)
                    self.assertEqual(record["step_check"]["effective_step"], step)
                    self.assertIn("RS_Check!AA2", format_match_details(record))
                    if len(tokens) == 2:
                        self.assertEqual([m["rule"] for m in record["instance_match"]["items"]],
                                         [RS_INST_GROUP_RULE, RS_INST_FUZZY_RULE])

    def test_shared_formula_uses_its_own_cached_na_or_blank_without_routes(self):
        # Shared-formula followers are allowed to omit the expression; never
        # substitute the master's result, or guess a name from the formula text.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shared.xlsx"
            for cached, expected_warnings in (("", ["EXCEL_FIELD_BLANK"]), ("NA", [])):
                with self.subTest(cached=cached):
                    _xlsx(path, cell_overrides={
                        "C1": '<c r="C1" t="str"><f t="shared" si="0" ref="C1:C2">K1</f><v>AAAA_BBB</v></c>',
                        "C2": f'<c r="C2" t="str"><f t="shared" si="0"/><v>{cached}</v></c>',
                        "E2": '<c r="E2"><v>1</v></c>',
                        "J2": '<c r="J2" t="str"><v>AAAA_BBB_C0</v></c>',
                    })
                    row, = self.check(*self.read(path)).rows
                    self.assertTrue(row.passed)
                    self.assertEqual(row.spec.rs_inst_formula_cell, "C2")
                    self.assertEqual(row.spec.rs_inst_items, ())
                    self.assertEqual(row.instances, ())
                    self.assertEqual([f.code for f in row.findings], expected_warnings)
            for marker in (None, 3, "C3", "A1:C2", "C0"):
                with self.subTest(bad_evidence=marker), self.assertRaisesRegex(GuiReportError, "RS_inst_formula_cell"):
                    load_validation_rows(json.dumps([dict(row.spec.as_dict(), RS_inst_formula_cell=marker)]))
            self.assertNotIn("RS_inst_formula_cell", self.base.as_dict())

    def test_alias_and_reordered_list_are_the_same_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.config.excel.columns)
                for pos, selectors in (("tile_core", "A,B"), ("top.u_tile", "B:\t A、A")):
                    writer.writerow(["IF", "rs_pipe", selectors, pos, 1, "clk_core", "rst_core", "crg_core", "NA", ""])
            with self.assertRaisesRegex(WorkbookError, "duplicate group"):
                self.read(path)

    def test_report_rejects_inconsistent_lists_but_reads_legacy_literals(self):
        report = self.check(replace(self.base, rs_inst="AAAA_BBB_C0；AAAA_BBB_X2", step=1))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            for mutate in (
                lambda row: row["spec"]["RS_inst_items"].reverse(),
                lambda row: row["instance_match"]["items"].pop(),
                lambda row: row["instance_match"]["items"][0].update(added_count=0),
                lambda row: row["instance_match"]["items"][1].update(matched_paths=[]),
            ):
                data = report_to_dict(report)
                mutate(data["rows"][0])
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(GuiReportError):
                    load_report(path)
            old = report_to_dict(self.check(self.ctrl))
            row = old["rows"][0]
            del row["spec"]["RS_inst_items"]
            del row["spec"]["RS_inst_split_rule"]
            del row["spec"]["selector_policy"]
            row["spec"]["RS_inst"] = row["instance_match"]["query"] = "legacy name"
            path.write_text(json.dumps(old), encoding="utf-8")
            self.assertEqual(load_report(path).rows[0]["spec"]["RS_inst"], "legacy name")

    def test_saved_report_uses_its_original_split_policy(self):
        raw = "AAAA_BBB  C0"
        spec = replace(self.base, rs_inst=raw, step=1)
        # Reproduce the previous release's selection and export using its real split rule.
        with patch("rscheck.model.split_rs_inst", lambda s: split_rs_inst(s, rule=RS_INST_LEGACY_SPLIT_RULE)):
            data = report_to_dict(self.check(spec))
        row = data["rows"][0]
        row["spec"]["RS_inst_split_rule"] = RS_INST_LEGACY_SPLIT_RULE
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            saved, = load_report(path).rows
            self.assertEqual(saved["spec"]["RS_inst_items"], [raw])
            self.assertEqual((saved["instance_match"]["selected"]["name"], saved["instance_match"]["best_distance"]),
                             ("AAAA_BBB_C0", 2))
            with self.assertRaisesRegex(GuiReportError, "split rule"):
                load_validation_rows(json.dumps([row["spec"]]))
            row["spec"]["RS_inst_items"] = ["AAAA_BBB", "C0"]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "original cell"):
                load_report(path)
            row["spec"]["RS_inst_items"] = [raw]
            row["spec"]["RS_inst_split_rule"] = RS_INST_SPLIT_RULE
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "original cell"):
                load_report(path)
            del row["spec"]["RS_inst_split_rule"]
            path.write_text(json.dumps(data), encoding="utf-8")
            saved, = load_report(path).rows
            self.assertEqual(saved["spec"]["RS_inst_items"], [raw])
            with self.assertRaisesRegex(GuiReportError, "original cell"):
                load_validation_rows(json.dumps([row["spec"]]))

    def test_file_delimiter_inside_quoted_cell_does_not_shift_following_columns(self):
        for ext, delimiter in ((".csv", ","), (".tsv", "\t")):
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ("quoted" + ext)
                raw = "AAAA_BBB_C0" + delimiter + "AAAA_BBB_X2"
                with path.open("w", newline="", encoding="utf-8-sig") as stream:
                    writer = csv.writer(stream, delimiter=delimiter)
                    writer.writerow(self.config.excel.columns)
                    writer.writerow(["IF", "rs_pipe", raw, "tile_core", 1, "clk_core", "rst_core", "crg_core",
                                     "NA", "core:: clk、ref"])
                spec, = self.read(path)
                self.assertEqual((spec.rs_inst, spec.rs_inst_items, spec.rs_route),
                                 (raw, ("AAAA_BBB_C0", "AAAA_BBB_X2"), "core:: clk、ref"))
                self.assertTrue(self.check(spec).passed)
