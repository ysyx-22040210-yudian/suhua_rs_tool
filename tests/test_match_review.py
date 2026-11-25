from __future__ import annotations

import csv
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

from rscheck.checker import check_specs
from rscheck.cli import main
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui_backend import GuiReportError, load_report
from rscheck.inventory import load_inventory
from rscheck.match_review import format_match_details
from rscheck.model import PositionInventory
from rscheck.reporting import report_to_dict


ROOT = Path(__file__).resolve().parents[1]


class MatchReviewTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config/rscheck.example.json")
        self.inv = load_inventory(ROOT / "examples/inventory.json")
        self.group, self.ctrl = read_spec_rows(ROOT / "examples/specs.csv", self.config.excel,
                                               self.config.position_mappings, self.config.crg_source_mappings)

    def record(self, row, inventory=None):
        result = check_specs((row,), inventory or self.inv, self.config.rtl, self.config.module_rules)
        return report_to_dict(result)["rows"][0]

    def test_complete_exact_and_prefix_evidence_including_wrong_module(self):
        review = format_match_details(self.record(self.group))
        self.assertIn("tile_core → top.u_tile", review)
        self.assertIn(self.config.rtl.suffix_regex, review)
        self.assertIn("有效拍数：5，Excel 期望：5", review)
        for n in range(6):
            line, = [s for s in review.splitlines() if s.startswith(f"  top.u_tile.AAAA_BBB_C{n} ")]
            self.assertIn(f'后缀 "_C{n}"', line)
            self.assertIn(f"贡献 {int(n != 2)} 拍", line)
            self.assertIn("examples/rtl/rs_example.sv:", line)
        original = self.inv.positions[self.group.position].instances[0]
        wrong = replace(original, module="wrong_type")
        inv = replace(self.inv, positions={self.group.position: PositionInventory(True, (wrong,))})
        record = self.record(replace(self.group, rs_inst=wrong.name, rs_route="unused"), inv)
        review = format_match_details(record)
        self.assertFalse(record["passed"])
        for text in ("完整名相同", "定义 wrong_type（与期望模块不同）", "贡献未确定", "RS_route 不参与"):
            self.assertIn(text, review)

    def test_per_item_decisions_overlap_and_tie_then_success(self):
        raw = "AAAA_BBB_X0：AAAA_BBB_X2、\tAAAA_BBB_C2"
        record = self.record(replace(self.group, rs_inst=raw, rs_route="unused", step=1))
        preview = format_match_details({"spec": record["spec"]})
        self.assertIn('RS_inst 提取 3 项："AAAA_BBB_X0"；"AAAA_BBB_X2"；"AAAA_BBB_C2"', preview)
        text = format_match_details(record)
        for detail in ('RS_inst 原文：' + json.dumps(raw, ensure_ascii=False), '[1] RS_inst："AAAA_BBB_X0"',
                       '距离 1  top.u_tile.AAAA_BBB_C0', '[2] RS_inst："AAAA_BBB_X2"', "贡献 0 拍",
                       '[3] RS_inst："AAAA_BBB_C2"', "与前项重叠，不重复计拍", "去重后选中 2 个"):
            self.assertIn(detail, text)
        text = format_match_details(self.record(replace(self.group, rs_inst="AAAA_BBB_X;AAAA_BBB_X2")))
        self.assertIn("6 个并列（显示 6/6）", text)
        self.assertIn("6. [并列未选] 距离 2  top.u_tile.AAAA_BBB_C5", text)
        self.assertIn("唯一最近：top.u_tile.AAAA_BBB_C2", text)
        self.assertIn("有效拍数：未确定", text)

    def test_route_tie_limit_is_stable_and_legacy_missing_candidates_are_not_invented(self):
        one = self.inv.positions[self.ctrl.position].instances[0]
        members = tuple(replace(one, name=f"UN_{i:02}", full_name=f"top.u_tile.UN_{i:02}") for i in range(60))
        route = replace(self.ctrl, rs_inst="", rs_route="UN_XX")
        def make(items):
            return self.record(route, replace(self.inv, positions={route.position: PositionInventory(True, items)}))
        record = make(members)
        self.assertEqual(record["instance_match"], make(tuple(reversed(members)))["instance_match"])
        text = format_match_details(record)
        for part in ("使用字段：RS_route", "60 个并列（显示 50/60）", "50. [并列未选] 距离 2  top.u_tile.UN_49",
                     "另有 10 个候选未展示", "有效拍数：未确定"):
            self.assertIn(part, text)
        self.assertNotIn("UN_50", text)
        del record["instance_match"]["extra_tied_candidates"]
        self.assertIn("60 个并列（显示 5/60）", format_match_details(record))

    def test_not_run_no_candidates_legacy_and_validation_are_distinct(self):
        for source in (replace(self.group, position="missing"), replace(self.ctrl, rs_inst="", position="missing")):
            text = format_match_details(self.record(source))
            self.assertIn("未执行：找不到指定层次", text)
            self.assertNotIn("候选排名", text)
        text = format_match_details(self.record(replace(self.ctrl, rs_inst="", rs_module="absent")))
        self.assertIn("没有同模块候选", text)
        record = self.record(self.group)
        del record["instance_match"]
        self.assertIn("旧报告", format_match_details(record))
        self.assertIn("尚未运行 RTL", format_match_details({"spec": record["spec"]}))
        self.assertIn("全局诊断", format_match_details({"spec": {"Intf_type": "GLOBAL", "row": "-"}}))

    def test_cli_and_exports_share_evidence_and_do_not_overwrite_inputs(self):
        src = ROOT / "examples/specs_route.csv"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            source = out / "check.csv"
            source.write_bytes(src.read_bytes().replace(b"CTRL_RS_DO", b"AAAA_BBB_C"))
            args = ["check", "--excel", str(source), "--config", str(ROOT / "config/rscheck.example.json"),
                    "--inventory", str(ROOT / "examples/inventory.json")]
            paths = [out / name for name in ("r.json", "r.csv", "result.txt")]
            buf = StringIO()
            with redirect_stdout(buf):
                self.assertEqual(main(args + ["--match-details", "--json-report", str(paths[0]), "--csv-report",
                                              str(paths[1]), "--match-report", str(paths[2])]), 1)
            report = load_report(paths[0])
            details = [format_match_details(row) for row in report.rows]
            with paths[1].open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            for row, expected in zip(rows, details):
                self.assertEqual(row["match_details"], expected)
                self.assertIn(expected, paths[2].read_text(encoding="utf-8"))
                self.assertIn(expected, buf.getvalue())
            before = source.read_bytes()
            with redirect_stderr(StringIO()):
                self.assertEqual(main(args + ["--match-report", str(source)]), 2)
            self.assertEqual(source.read_bytes(), before)
            data = json.loads(paths[0].read_text(encoding="utf-8"))
            match = data["rows"][0]["instance_match"]
            match["extra_tied_candidates"][0] = match["top_candidates"][0]
            paths[0].write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "repeated tied candidates"):
                load_report(paths[0])
