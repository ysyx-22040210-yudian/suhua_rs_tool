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
from rscheck.gui_backend import GuiReportError, load_report
from rscheck.inventory import load_inventory
from rscheck.match_review import format_match_details
from rscheck.reporting import report_to_dict, write_csv_report, write_json_report
from tests.test_excel_reader import _xlsx


ROOT = Path(__file__).resolve().parents[1]


class Route2Tests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(ROOT / "config/rscheck.example.json")
        self.inv = load_inventory(ROOT / "examples/inventory.json")
        self.rows = self.read(ROOT / "examples/specs_route2.csv")

    def read(self, file, **columns):
        return read_spec_rows(file, replace(self.cfg.excel, columns={**self.cfg.excel.columns, **columns}),
                              self.cfg.position_mappings, self.cfg.crg_source_mappings)

    def check(self, *rows):
        return check_specs(rows, self.inv, self.cfg.rtl, self.cfg.module_rules)

    def test_scoped_duplicate_uses_whole_unicode_reference_with_aliases_and_export(self):
        a,b = self.rows
        report = self.check(a,b)
        self.assertEqual((report.status,report.warning_count), ("PASS",0))
        with tempfile.TemporaryDirectory() as tmp:
            data = load_report(write_json_report(report,Path(tmp)/"r.json")).rows
            with write_csv_report(report,Path(tmp)/"r.csv").open(encoding="utf-8-sig") as f:
                exported = list(csv.DictReader(f))
        for idx, name in enumerate(("AAAA_BBB_C2","CTRL_RS_D0")):
            spec, match = data[idx]["spec"],data[idx]["instance_match"]
            self.assertEqual((spec["route_group_size"], match["mode"],match["query"]),
                             (2, "RS_route2",self.rows[idx].rs_route2))
            self.assertEqual(match["selected"]["name"],name)
            self.assertEqual(match["primary_attempts"][0]["status"],"ambiguous")
            self.assertEqual(exported[idx]["RS_route2"],match["query"])
            self.assertIn("三项在 2 行相同",format_match_details(data[idx]))

    def test_uniqueness_requires_all_three_fields_and_skips_empty_selectors(self):
        a,b = self.rows
        a = replace(a,rs_route="AAAA_BBB_C2",rs_route2="CTRL_RS_D0")
        b = replace(b,rs_route=a.rs_route)
        for change in ({"position":"top.u_tile_peer"}, {"rs_module":"rs_plain"},
                       {"rs_route":"aaaa_bbb_C2"},{"sheet":"other"},{"rs_inst":"NA"}):
            with self.subTest(change=change):
                record = report_to_dict(self.check(a,replace(b,**change)))["rows"][0]
                self.assertEqual((record["spec"]["route_group_size"],record["instance_match"]["mode"],
                                  record["instance_match"]["selected"]["name"]), (1, "RS_route","AAAA_BBB_C2"))
        # A peer with a populated name can be a member of the Excel group,
        # but its own successful match must never use route2.
        record = report_to_dict(self.check(a,replace(b,rs_inst="CTRL_RS_DO")))["rows"]
        self.assertEqual([r["instance_match"]["mode"] for r in record],["RS_route2","RS_inst"])

    def test_missing_and_ambiguous_secondary_never_reuse_shared_primary(self):
        a,b = self.rows
        for value,code in (("","RS_ROUTE2_REQUIRED"),("NA","RS_ROUTE2_REQUIRED"),
                           ("AAAA_BBB_X","RS_ROUTE2_AMBIGUOUS")):
            with self.subTest(value=value):
                row = self.check(replace(a,rs_route2=value),b).rows[0]
                self.assertFalse(row.passed)
                self.assertFalse(row.instances)
                self.assertIn(code,[f.code for f in row.findings])
                self.assertEqual("EXCEL_FIELD_BLANK" in [f.code for f in row.findings],value=="")
        disabled = self.check(replace(a, rs_route2="", rs_route2_mapped=False), b).rows[0]
        self.assertIn("RS_ROUTE2_REQUIRED", [f.code for f in disabled.findings])
        self.assertNotIn("EXCEL_FIELD_BLANK", [f.code for f in disabled.findings])
        missing = self.check(*(replace(r, rs_module="not_in_elab") for r in (a,b))).rows[0]
        self.assertEqual(missing.route_field_used, "RS_route2")
        self.assertIn("RS_ROUTE2_NO_CANDIDATES", [f.code for f in missing.findings])

    def test_saved_report_rejects_partial_primary_disguised_as_route_fallback(self):
        report = report_to_dict(self.check(*self.rows))
        row = report["rows"][0]
        row["instance_match"]["primary_attempts"][0]["status"] = "matched"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "altered.json"
            path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "all RS_inst attempts"):
                load_report(path)

    def test_multi_all_miss_falls_back_once_partial_never_uses_route_or_counts_step(self):
        a,b = self.rows
        r = self.check(replace(a,rs_inst="AAAA_BBB_X;AAAA_BBB_Y;AAAA_BBB_X"),b)
        m = report_to_dict(r)["rows"][0]["instance_match"]
        self.assertEqual((m["mode"],len(m["primary_attempts"]),len(r.rows[0].instances)),("RS_route2",3,1))
        for filled in ("AAAA_BBB_C1","AAAA_BBB_X1"):
            row = self.check(replace(a,rs_inst=filled+"；AAAA_BBB_X"),b).rows[0]
            self.assertFalse(row.passed)
            self.assertIsNone(row.effective_step)
            self.assertEqual([i.name for i in row.instances],["AAAA_BBB_C1"])
            self.assertEqual([f.code for f in row.findings],["RS_INST_AMBIGUOUS"])
            self.assertEqual(row.route_field_used,"")
        for blank in ("", "  NA  ", ";，\t"):
            row = self.check(replace(a,rs_inst=blank),b).rows[0]
            self.assertFalse(row.instances)
            self.assertIsNone(row.effective_step)
            self.assertEqual(row.spec.route_group_size,0)

    def test_conditional_formula_errors_and_cached_mixed_text_in_custom_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"route.xlsm"
            def candidate(inst, k):
                _xlsx(path,cell_overrides={"C2":f'<c r="C2" t="str"><v>{inst}</v></c>',
                     "J2":'<c r="J2" t="str"><v>AAAA_BBB_C2</v></c>', "AA2":k})
                return self.read(path,RS_route2=27)[0]
            for cell in ('<c r="AA2" t="e"><f>NA()</f><v>#N/A</v></c>',
                         '<c r="AA2"><f>NOT_CACHED()</f><v/></c>'):
                for inst in ("", "CTRL_RS_D0", "CTRL_RS_DO", "AAAA_BBB_X"):
                    s=candidate(inst,cell)
                    self.assertNotIn("ROUTE_CELL_ERROR",[f.code for f in self.check(s).rows[0].findings])
                s=candidate("AAAA_BBB_X",cell)
                peer=replace(s, row_number=3, rs_route2="控制：CTRL_RS_D0", route2_cell_error="")
                err=next(f for f in self.check(s,peer).rows[0].findings if f.code=="ROUTE_CELL_ERROR")
                self.assertIn("RS_Check!AA2 (RS_route2)",err.message)
            s=candidate("AAAA_BBB_X",'<c r="AA2" t="str"><f>A1&amp;B1</f><v>控制：CTRL_RS_D0</v></c>')
            peer=replace(s,row_number=3,rs_route2="数据：AAAA_BBB_C2")
            self.assertEqual(self.check(s,peer).rows[0].instances[0].name,"CTRL_RS_D0")
            for name, needs_route in (("", False), ("CTRL_RS_D0", False), ("CTRL_RS_DO", False),
                                      ("AAAA_BBB_C1；AAAA_BBB_X", False), ("AAAA_BBB_X", True)):
                _xlsx(path, cell_overrides={
                    "C2": f'<c r="C2" t="str"><v>{name}</v></c>',
                    "J2": '<c r="J2" t="e"><f>NA()</f><v>#N/A</v></c>',
                })
                result = self.check(*self.read(path)).rows[0]
                errors = [f for f in result.findings if f.code == "ROUTE_CELL_ERROR"]
                self.assertEqual(len(errors), int(needs_route))
                if errors:
                    self.assertIn("RS_Check!J2 (RS_route)", errors[0].message)
