from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from rscheck.checker import check_specs
from rscheck.cli import main
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui_backend import GuiReportError, load_report, load_validation_result
from rscheck.inventory import load_inventory
from rscheck.model import REQUIRED_ROW_FIELDS, WorkbookError
from rscheck.reporting import write_json_report
from tests.test_excel_reader import _xlsx

ROOT = Path(__file__).resolve().parents[1]


class FieldPolicyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)
        self.cfgpath = ROOT / "config/rscheck.example.json"
        self.cfg = load_config(self.cfgpath)
        self.inv = load_inventory(ROOT / "examples/inventory.json")
        self.data = dict(Intf_type="IF", RS_module="rs_pipe", RS_inst="AAAA_BBB", position="tile_core",
                         step=5, clk="clk_core", rst="rst_core", CRG_source="crg_core", RS_CFG_EN="假门控", RS_route="NA")

    def table(self, *changes, suffix=".csv"):
        book = self.out / ("spec"+suffix)
        with book.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, self.data, delimiter="\t" if suffix == ".tsv" else ",")
            writer.writeheader()
            for change in changes:
                writer.writerow({**self.data, **change})
        return book

    def read(self, book, on_skip=None):
        return read_spec_rows(book, self.cfg.excel, self.cfg.position_mappings, self.cfg.crg_source_mappings,
                              on_skip=on_skip)

    def result(self, *changes):
        report = check_specs(self.read(self.table(*changes)), self.inv, self.cfg.rtl, self.cfg.module_rules)
        return load_report(write_json_report(report, self.out/"report.json"))

    def test_each_row_gate_is_checked_before_alias_step_and_duplicate_groups(self):
        for ext in (".csv", ".tsv"):
            bad = [{**{"step": "invalid"}, field: marker} for field in REQUIRED_ROW_FIELDS for marker in (" \t", " NA ")]
            skipped = []
            rows = self.read(self.table(*bad, {"step": 0}, suffix=ext), skipped.append)
            self.assertEqual([(s.row_number, s.step) for s in rows], [(10, 0)])
            self.assertEqual([s.row for s in skipped], list(range(2, 10)))
            self.assertEqual([s.fields for s in skipped],
                             [{f: r} for f in REQUIRED_ROW_FIELDS for r in ("blank", "NA")])
        for marker in ("na", "N/A"):
            with self.assertRaisesRegex(WorkbookError, "non-negative integer"):
                self.read(self.table({"step": marker}))
        self.assertEqual(self.result({"step": 0}).rows[0]["findings"][0]["code"], "STEP_MISMATCH")

    def test_formula_skip_precedes_even_broken_caches_in_other_gate_or_optional_field(self):
        f = self.out/"formulas.xlsx"
        bad = {"B2": '<c r="B2"><f>1+2</f><v/></c>', "G2": '<c r="G2" t="e"><f>NA()</f><v>#N/A</v></c>'}
        for cache, reason in (("<v/>", "blank"), ("<v> NA </v>", "NA")):
            _xlsx(f, cell_overrides={**bad, "E2": '<c r="E2" t="str"><f>K2</f>'+cache+'</c>'})
            skipped = []
            self.assertEqual(self.read(f, skipped.append), [])
            self.assertEqual(skipped[0].as_dict(), {"sheet":"RS_Check", "row":2, "fields":{"step":reason}})
        _xlsx(f, cell_overrides={"G2":bad["G2"]})
        with self.assertRaisesRegex(WorkbookError, r"G2 \(rst\).*#N/A"):
            self.read(f)
        _xlsx(f, cell_overrides={"F2":'<c r="F2" t="str"><f>K2</f><v/></c>',
                                 "G2":'<c r="G2" t="str"><f>L2</f><v>NA</v></c>'})
        self.assertEqual(self.read(f)[0].ignored_fields, {"clk":"blank", "rst":"NA", "RS_route":"blank"})

    def test_all_six_fields_blank_warn_once_independently_or_na_is_silent(self):
        for f in ("RS_inst", "clk", "rst", "CRG_source", "RS_CFG_EN", "RS_route"):
            for marker, warnings in (("  ", 1), (" NA ", 0)):
                with self.subTest(field=f, marker=marker):
                    change = {"RS_inst":"CTRL_RS_D0", "step":1, "clk":"clk_aux", "rst":"rst_aux", "CRG_source":"crg_aux"}
                    if f == "RS_inst":
                        change["RS_route"] = "CTRL_RS_DO"
                    row, = self.result({**change, f:marker}).rows
                    self.assertTrue(row["passed"])
                    self.assertEqual([(i["code"], i["actual"]["field"]) for i in row["findings"]],
                                     [("EXCEL_FIELD_BLANK", f)]*warnings)
                    self.assertEqual(row["step_check"]["effective_step"], 1)
        row, = self.result({"clk":"", "RS_CFG_EN":"", "rst":"NA", "RS_route":""}).rows
        self.assertEqual(row["step_check"]["physical_instances"], 6)
        self.assertEqual([i["actual"]["field"] for i in row["findings"]], ["clk", "RS_CFG_EN", "RS_route"])
        self.assertEqual(row["source_signal_checks"], [])
        self.assertEqual(row["crg_source_check"]["status"], "pass")

    def test_gating_each_signal_still_validates_other_signal_and_step(self):
        for name, other, formal in (("clk","rst","rst"), ("rst","clk","clk")):
            r, = self.result({name:"NA", other:"WRONG", "step":99}).rows
            self.assertEqual([s["role"] for s in r["source_signal_checks"]], [other]*6)
            self.assertEqual({f["code"] for f in r["findings"]}, {other.upper()+"_CONNECTION_MISMATCH","STEP_MISMATCH"})
            self.assertEqual({e["trace_role"] for e in r["crg_source_check"]["instances"]}, {formal})
        invalid_target, = self.result({"clk":"NA","CRG_source":"no.such.path"}).rows
        self.assertEqual({f["code"] for f in invalid_target["findings"]}, {"CRG_SOURCE_NOT_FOUND"})
        not_checked, = self.result({"CRG_source":"NA", "clk":"WRONG","rst":"WRONG"}).rows
        self.assertEqual((not_checked["crg_source_check"]["status"], not_checked["source_signal_status"]), ("not_run","not_run"))
        self.assertFalse(not_checked["findings"])

    def test_invalid_target_marker_does_not_shadow_a_database_alias(self):
        path = self.table({"CRG_source":" NA "})
        row, = read_spec_rows(path, self.cfg.excel, {}, {"NA":"top.bogus"})
        self.assertEqual((row.crg_source,row.crg_source_alias), ("NA",""))

    def test_two_missing_selectors_do_not_prevent_checking_scope_and_module_or_guess_group(self):
        changes = {"RS_inst":"NA", "RS_route":"NA"}
        report = self.result(changes, changes, {**changes,"position":"missing"}, {**changes,"RS_module":"absent"})
        self.assertEqual((report.summary["rows"],report.summary["partial_rows"],report.summary["passed_rows"],
                          report.summary["failed_rows"],report.summary["warnings"]), (4,2,0,2,0))
        for row in report.rows:
            self.assertEqual(row["instance_match"]["mode"],"none")
            self.assertIsNone(row["step_check"]["effective_step"])
            self.assertFalse(row["matched_instances"])
        self.assertEqual([f["code"] for r in report.rows for f in r["findings"]], ["POSITION_NOT_FOUND","RS_MODULE_NOT_FOUND"])

    def test_skipped_rows_are_not_submitted_to_npi_and_all_skipped_writes_audit_only(self):
        for some in (False, True):
            book = self.table({"position":"NA"}, *([{}] if some else []))
            args = ["--excel",str(book),"--config",str(self.cfgpath)]
            dest = self.out/"live.json"
            csv_report = self.out/"live.csv"
            evidence = self.out/"snapshot.json"
            evidence.write_text("KEEP",encoding="utf-8")
            with patch("rscheck.cli.collect_inventory",return_value=self.inv) as collector, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["check",*args,"--collector","collector","--elab-db","test.elab++",
                                       "--keep-inventory",str(evidence),"--json-report",str(dest),"--csv-report",str(csv_report)]),0)
            self.assertEqual(collector.call_count, int(some))
            if some:
                self.assertEqual([s.row_number for s in collector.call_args[0][1]], [3])
            self.assertEqual(evidence.read_text(encoding="utf-8"),"KEEP")
            report=load_report(dest)
            self.assertEqual(report.summary, {"passed":True,"rows":int(some),"passed_rows":int(some),"failed_rows":0,
                                             "errors":0,"warnings":0,"skipped_rows":1})
            self.assertEqual(report.raw["skipped_rows"], [{"sheet":"CSV","row":2,"fields":{"position":"NA"}}])
            with csv_report.open(encoding="utf-8-sig", newline="") as f:
                self.assertEqual(next(csv.DictReader(f))["status"],"SKIPPED")
            out=io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["validate",*args,"--json","--include-skipped"]),0)
            rows, skipped = load_validation_result(out.getvalue())
            self.assertEqual((len(rows),list(skipped)),(int(some),report.raw["skipped_rows"]))

    def test_gui_rejects_inconsistent_field_policy_in_saved_data(self):
        row = self.result({"clk":"NA"}).rows[0]
        row["spec"]["ignored_fields"]["rst"] = "NA"
        with self.assertRaisesRegex(GuiReportError, "disagrees with rst"):
            load_validation_result(json.dumps([row["spec"]]))

    def test_unused_empty_rows_are_not_errors_or_phantom_skip_warnings(self):
        all_empty = {k: "" for k in self.data}
        path = self.table(all_empty, {"step":"NA"}, all_empty)
        dropped = []
        self.assertEqual(self.read(path, dropped.append), [])
        self.assertEqual([s.row for s in dropped], [3])
        self.assertEqual(self.read(self.table(all_empty)), [])
        with self.assertRaisesRegex(WorkbookError, "no specification rows"):
            self.read(self.table())
        # A comment in an unrelated business column is a data row, but does
        # not make any mapped field active or emit a warning.
        path = self.table()
        with path.open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow([""]*10 + ["备注"])
        dropped.clear()
        self.assertEqual(self.read(path,dropped.append), [])
        self.assertEqual(dropped[0].fields, dict.fromkeys(REQUIRED_ROW_FIELDS, "blank"))
