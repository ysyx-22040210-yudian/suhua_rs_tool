from __future__ import annotations

import csv
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from xml.sax.saxutils import escape

from rscheck.checker import check_specs
from rscheck.cli import main
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui_backend import GuiReportError, load_report, load_validation_result
from rscheck.inventory import load_inventory
from rscheck.model import WorkbookError
from tests.test_excel_reader import _xlsx


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/rscheck.example.json"
SAMPLE = ROOT / "examples/specs_merged_steps.xlsx"


def text_cell(ref, value):
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'


def num(ref, value):
    return f'<c r="{ref}"><v>{value}</v></c>'


def spec(row, instance, step):
    cells = {letter + str(row): text_cell(letter + str(row), value) for letter, value in zip(
        "ABCDFGHIJ", ("IF", "rs_pipe", instance, "tile_core", "NA", "NA", "NA", "NA", "NA"))}
    cells[f"E{row}"] = num(f"E{row}", step)
    return cells


def wrong_total(path):
    # Despite a purely Excel discrepancy, both RTL rows should have the correct effective step.
    _xlsx(path, cell_overrides={**spec(2, "AAAA_BBB_C0", 1), **spec(3, "AAAA_BBB_C2", 0),
                                "L2": num("L2", 2)}, merge_refs=("L2:L3",))


class StepSumTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / "book.xlsx"
        self.cfg = load_config(CONFIG)
        self.inv = load_inventory(ROOT / "examples/inventory.json")

    def read(self, path=None, column=12, on_skip=None, **cfg):
        columns = dict(self.cfg.excel.columns)
        if column:
            columns["step_sum"] = column
        return read_spec_rows(path or self.path, replace(self.cfg.excel, columns=columns, **cfg),
                              self.cfg.position_mappings, self.cfg.crg_source_mappings, on_skip=on_skip)

    def cli(self, action, *options):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([action, "--excel", str(self.path), "--config", str(CONFIG),
                       "--column", "step_sum=12", *options])
        return rc, out.getvalue(), err.getvalue()

    def test_native_sample_two_equal_totals_skipped_numeric_anchor_and_formula(self):
        skipped = []
        rows = self.read(SAMPLE, on_skip=skipped.append)
        self.assertEqual([s.row for s in skipped], [4])
        checks = [(s.row_number, s.step_sum_check) for s in rows if s.step_sum_check]
        self.assertEqual([(n, c.cell_range, c.expected, c.members) for n, c in checks], [
            (2, "RS_Check!L2:L3", 2, ((2, 2), (3, 0))),
            (5, "RS_Check!L4:L6", 2, ((5, 1), (6, 1)))])
        report = check_specs(rows, self.inv, self.cfg.rtl, self.cfg.module_rules)
        self.assertTrue(report.passed)
        self.assertEqual([r.effective_step for r in report.rows], [2, 0, 1, 1])

    def test_wrong_total_in_first_merge_does_not_mark_second_merge_failed(self):
        rows = list(self.read(SAMPLE))
        first = rows[0]
        rows[0] = replace(first, step_sum_check=replace(first.step_sum_check, expected=99))
        report = check_specs(rows, self.inv, self.cfg.rtl, self.cfg.module_rules)
        self.assertFalse(report.passed)
        self.assertEqual([r.spec.row_number for r in report.rows if not r.passed], [2])
        self.assertEqual([(r.spec.step_sum_check.cell_range, r.spec.step_sum_check.as_dict()["status"])
                          for r in report.rows if r.spec.step_sum_check],
                         [("RS_Check!L2:L3","mismatch"), ("RS_Check!L4:L6","pass")])

    def test_participation_gate_and_no_expansion_of_sparse_ranges(self):
        cells = {**spec(2, "AAAA_BBB_C1", 900), "A2": text_cell("A2", "NA"),
                 **spec(5, "AAAA_BBB_C2", 0), "L2": '<c r="L2"><f>SUM(E5)</f><v>0E+00</v></c>',
                 **spec(11, "not_checked", 42), "D11": text_cell("D11", ""),
                 "L11": '<c r="L11"><f>1/0</f></c>'}
        _xlsx(self.path, cell_overrides=cells, merge_refs=("L11:L1048576", "L2:L10"))
        row, = self.read()
        self.assertEqual((row.row_number, row.step_sum_check.expected, row.step_sum_check.members), (5, 0, ((5, 0),)))
        self.assertEqual(row.step_sum_check.as_dict()["status"], "pass")

    def test_anchor_formulas_on_skipped_rows_use_active_descendants_or_ignore(self):
        cells = {**spec(2, "AAAA_BBB_C2", 77), "B2": text_cell("B2", "NA"),
                 **spec(3, "AAAA_BBB_C3", 1), "L2": '<c r="L2"><f>SUM(E3)</f></c>'}
        _xlsx(self.path, cell_overrides=cells, merge_refs=("L2:L3",))
        with self.assertRaisesRegex(WorkbookError, r"RS_Check!L2 \(step_sum\): formula has no cached"):
            self.read()
        cells["B3"] = text_cell("B3", "NA")
        _xlsx(self.path, cell_overrides=cells, merge_refs=("L2:L3",))
        self.assertEqual(self.read(), [])

    def test_blank_and_na_check_once_only_and_do_not_inherit_unmerged(self):
        cells = {**spec(2, "one", 4), **spec(3, "two", 5), **spec(4, "three", 2),
                 **spec(5, "four", 7), **spec(6, "five", 0), **spec(7, "six", 1),
                 "L4": text_cell("L4", " NA "), "L6": num("L6", 0)}
        _xlsx(self.path, cell_overrides=cells, merge_refs=("L2:L3","L4:L5"))
        rows = self.read()
        self.assertEqual([r.step_sum_check.as_dict()["status"] if r.step_sum_check else None for r in rows],
                         ["blank", None, "NA", None, "pass", "blank"])
        self.assertEqual([(f.code, f.row_number) for r in rows for f in r.workbook_findings],
                         [("STEP_SUM_BLANK", 2), ("STEP_SUM_BLANK", 7)])

    def test_non_contiguous_mapping_header_and_offsets(self):
        cells = {**spec(5, "one", 2), **spec(8, "two", 3), "AA3": text_cell("AA3", "系统总拍数"),
                 "AA5": '<c r="AA5"><f>SUM(E5:E8)</f><v>5E+00</v></c>'}
        _xlsx(self.path, cell_overrides=cells, merge_refs=("A1:H1","AA1:AC1","AA5:AA8"))
        rows = self.read(column=27, sheet=1, header_row=3, data_start_row=5)
        self.assertEqual(rows[0].step_sum_check.as_dict(),
                         {"range":"RS_Check!AA5:AA8","expected":5,"actual":5,"status":"pass",
                          "members":[{"row":5,"step":2},{"row":8,"step":3}]})
        self.assertIsNone(rows[1].step_sum_check)

    def test_malformed_merges_and_unmapped_columns(self):
        for spans in (("K2:L2",), ("L2:M3",), ("L1:L2",), ("L2:L5","L4:L7")):
            with self.subTest(spans=spans):
                _xlsx(self.path, merge_refs=spans)
                with self.assertRaisesRegex(WorkbookError, "step_sum"):
                    self.read()
                self.assertIsNone(self.read(column=None)[0].step_sum_check)
        _xlsx(self.path, cell_overrides={"L2": '<c r="L2" t="e"><f>1/0</f><v>#DIV/0!</v></c>'},
              merge_refs=("L2:L3",))
        self.assertIsNone(self.read(column=None)[0].step_sum_check)
        with self.assertRaisesRegex(WorkbookError, "step_sum.*#DIV/0!"):
            self.read()

    def test_reject_fraction_negative_bool_uncached_and_text_formula_not_execution(self):
        for cell in (num("L2", "1.5"), num("L2", "-1"), '<c r="L2" t="b"><v>1</v></c>',
                     '<c r="L2"><f>SUM(E2:E3)</f></c>', text_cell("L2", "=SUM(E2:E3)")):
            with self.subTest(cell=cell):
                _xlsx(self.path, cell_overrides={"L2": cell}, merge_refs=("L2:L3",))
                with self.assertRaisesRegex(WorkbookError, "RS_Check!L2.*step_sum"):
                    self.read()

    def test_csv_singletons_never_forward_fill(self):
        for ext, delimiter in ((".csv", ","), (".tsv", "\t")):
            path = self.path.with_suffix(ext)
            with path.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter=delimiter)
                w.writerow([""]*12)
                for i, total in enumerate(("3", "", "NA"), start=1):
                    w.writerow(["IF","rs_pipe",str(i),"tile_core",3,"NA","NA","NA","NA","NA","",total])
            self.assertEqual([r.step_sum_check.as_dict()["status"] for r in self.read(path)], ["pass","blank","NA"])

    def test_mismatch_on_validate_and_check_with_evidence_and_correct_rtl(self):
        wrong_total(self.path)
        rc, out, err = self.cli("validate", "--include-skipped", "--json")
        self.assertEqual(rc, 1)
        rows, _ = load_validation_result(out)
        self.assertEqual(rows[0]["step_sum_check"]["expected"],2)
        self.assertIn("RS_Check!L2:L3", err)
        self.assertEqual(err.count("STEP_SUM_MISMATCH"), 1)
        report_path, csv_path = self.path.with_suffix(".json"), self.path.with_suffix(".csv")
        rc, _, _ = self.cli("check","--inventory",str(ROOT / "examples/inventory.json"),
                            "--json-report",str(report_path),"--csv-report",str(csv_path))
        self.assertEqual(rc, 1)
        report = load_report(report_path)
        self.assertEqual(report.summary["errors"], 1)
        self.assertEqual([r["step_check"]["effective_step"] for r in report.rows], [1, 0])
        self.assertEqual(report.rows[0]["findings"][0]["code"], "STEP_SUM_MISMATCH")
        self.assertNotIn("step_sum_check", report.rows[1]["spec"])
        with csv_path.open(encoding="utf-8-sig",newline="") as f:
            entries=list(csv.DictReader(f))
        self.assertEqual(json.loads(entries[0]["step_sum_check"]), rows[0]["step_sum_check"])
        self.assertFalse(entries[1]["step_sum_check"])
        # A report with stale totals cannot present them as a PASS in the GUI.
        raw = json.loads(report_path.read_text(encoding="utf-8"))
        raw["rows"][0]["spec"]["step_sum_check"]["status"] = "pass"
        report_path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(GuiReportError,"step_sum_check"):
            load_report(report_path)
        self.assertEqual(self.cli("validate","--column","step_sum=")[0],0)

    def test_group_pass_cannot_hide_compensating_individual_step_errors(self):
        _xlsx(self.path, cell_overrides={**spec(2, "AAAA_BBB_C0", 0), **spec(3, "AAAA_BBB_C2", 1),
                                       "L2":num("L2",1)}, merge_refs=("L2:L3",))
        self.assertEqual(self.cli("validate")[0], 0)
        report = check_specs(self.read(), self.inv, self.cfg.rtl, self.cfg.module_rules)
        self.assertEqual([f.code for r in report.rows for f in r.findings], ["STEP_MISMATCH","STEP_MISMATCH"])
        self.assertFalse(report.passed)
