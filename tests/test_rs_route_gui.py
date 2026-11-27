"""Mapped Tk end-to-end check. Set RSCHECK_ROUTE_COLLECTOR/KDB for live NPI."""
from __future__ import annotations

import csv
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import INVENTORY_SOURCE, LIVE_SOURCE, load_report
from rscheck.model import RS_INST_FUZZY_RULE, RS_INST_GROUP_RULE
from tests.test_excel_reader import _xlsx


ROOT = Path(__file__).resolve().parents[1]


class RouteGuiTests(unittest.TestCase):
    def test_merged_steps_in_mapped_window(self):
        self._exercise_window("step_sum")

    def test_route2_scoped_duplicate_then_partial_list_in_mapped_window(self):
        self._exercise_window("route2")

    def test_blank_and_na_field_policy_in_mapped_window(self):
        self._exercise_window("field_policy")

    def test_source_signals_at_crg_in_mapped_window(self):
        self._exercise_window("source_signals")

    def test_route_result_and_evidence_in_mapped_window(self):
        self._exercise_window("route")

    def test_manual_match_details_in_mapped_window(self):
        self._exercise_window("match_review")

    def test_rs_inst_fuzzy_fallback_in_mapped_window(self):
        self._exercise_window("inst_fallback")

    def test_multi_inst_cell_in_mapped_window(self):
        self._exercise_window("inst_list")

    def test_multiline_rs_inst_in_mapped_window(self):
        self._exercise_window("inst_multiline")

    def test_semicolon_backslash_newline_in_mapped_window(self):
        self._exercise_window("continuation")

    def test_multi_inst_fuzzy_matching_in_mapped_window(self):
        self._exercise_window("inst_list_fuzzy")

    def test_validate_then_check_formula_workbook_in_mapped_window(self):
        self._exercise_window("formulas")

    def _exercise_window(self, scenario):
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

                def invoke_and_wait(button, state):
                    button.invoke()
                    deadline = time.monotonic() + 120
                    while app.running and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.02)
                    self.assertFalse(app.running, "GUI timed out")
                    if state != "ERROR":
                        self.assertFalse(error.called, str(error.call_args))
                    self.assertEqual(app.summary_state_var.get(), state, app.log_text.get("1.0", "end")[-2000:])
                    self.assertFalse(app.controller.is_running)

                if scenario == "step_sum":
                    from tests.test_step_sum import SAMPLE, wrong_total
                    self.assertEqual(app.column_vars["step_sum"].get(), "")
                    app.column_vars["step_sum"].set("12")
                    app.excel_var.set(str(SAMPLE))
                    invoke_and_wait(app.validate_button, "VALID")
                    self.assertEqual(app.summary_rows_var.get(), "行数 4 · 跳过 1")
                    app.result_tree.selection_set("validation-2")
                    app._on_result_selected()
                    self.assertIn("L4:L6", app.review_text.get("1.0", "end"))
                    self.assertIn("第5行=1，第6行=1", app.review_text.get("1.0", "end"))
                    invoke_and_wait(app.run_button, "PASS")
                    self.assertEqual(load_report(app.json_report_var.get()).summary["warnings"], 0)
                    invalid = out / "wrong-total.xlsx"
                    wrong_total(invalid)
                    app.excel_var.set(str(invalid))
                    invoke_and_wait(app.validate_button, "FAIL")
                    self.assertEqual(app.summary_errors_var.get(), "错误 1")
                    self.assertEqual(app.result_tree.item("validation-0", "values")[0], "FAIL")
                    self.assertIn("合计格=2，参与行 step 之和=1", app.review_text.get("1.0", "end"))
                    invoke_and_wait(app.run_button, "FAIL")
                    row = load_report(app.json_report_var.get()).rows[0]
                    self.assertEqual(row["findings"][0]["code"], "STEP_SUM_MISMATCH")
                    self.assertEqual(row["step_check"]["effective_step"], 1)
                    return

                if scenario == "field_policy":
                    book = out / "fields.csv"
                    app.excel_var.set(str(book))
                    def write(*rows):
                        with book.open("w", encoding="utf-8-sig", newline="") as stream:
                            w = csv.writer(stream)
                            w.writerow(["接口", "模块", "例化", "层次", "拍数", "时钟", "复位", "来源", "门控", "参考名"])
                            w.writerows(rows)
                    omit = ["IF","NA","NA","never.collect",999,"","","","",""]
                    def active(clk, rst):
                        return ["IF","rs_pipe","AAAA_BBB_C2","tile_core",0,clk,rst,"crg_core","NA","NA"]
                    write(omit, active("", "rst_core"))
                    invoke_and_wait(app.validate_button, "VALID")
                    self.assertEqual(app.summary_rows_var.get(),"行数 1 · 跳过 1")
                    self.assertEqual(app.summary_warnings_var.get(),"警告 1")
                    self.assertEqual(app.result_tree.set("validation-0","row"), "3")
                    invoke_and_wait(app.run_button, "PASS")
                    record, = load_report(app.json_report_var.get()).rows
                    self.assertEqual([(s["role"],s["status"]) for s in record["source_signal_checks"]], [("rst","matched")])
                    self.assertEqual([(f["code"],f["actual"]["field"]) for f in record["findings"]], [("EXCEL_FIELD_BLANK","clk")])
                    self.assertEqual(record["step_check"]["effective_step"], 0)
                    self.assertIn("clk 为空", app.review_text.get("1.0","end"))
                    write(omit, active("clk_rs","NA"))
                    invoke_and_wait(app.run_button, "FAIL")
                    self.assertEqual(app.summary_warnings_var.get(),"警告 0")
                    self.assertEqual([f["code"] for f in load_report(app.json_report_var.get()).rows[0]["findings"]],
                                     ["CLK_CONNECTION_MISMATCH"])
                    write(omit)
                    invoke_and_wait(app.validate_button, "SKIPPED")
                    invoke_and_wait(app.run_button, "SKIPPED")
                    self.assertEqual(app.summary_pass_var.get(),"通过 0")
                    self.assertEqual(app.summary_rows_var.get(),"行数 0 · 跳过 1")
                    self.assertEqual(len(app.result_tree.get_children()), 0)
                    return

                if scenario == "source_signals":
                    book = out / "source.xlsx"
                    app.excel_var.set(str(book))
                    for clk, rst, state in (("clk_core", "rst_core", "PASS"), ("clk_rs", "rst_rs", "FAIL")):
                        _xlsx(book, cell_overrides={
                            "C2": '<c r="C2" t="inlineStr"><is><t>AAAA_BBB_C0</t></is></c>',
                            "E2": '<c r="E2"><v>1</v></c>',
                            "F2": f'<c r="F2" t="inlineStr"><is><t>{clk}</t></is></c>',
                            "G2": f'<c r="G2" t="inlineStr"><is><t>{rst}</t></is></c>',
                        })
                        invoke_and_wait(app.run_button, state)
                        app.result_tree.selection_set("row-0")
                        app._on_result_selected()
                        evidence = json.loads(app.evidence_text.get("1.0", "end"))
                        clock, reset = evidence["source_signal_checks"]
                        self.assertEqual((clock["endpoints"][0]["depth"], reset["endpoints"][0]["depth"]), (3, 2))
                        self.assertEqual([s["status"] for s in (clock, reset)],
                                         ["matched" if state == "PASS" else "mismatch"] * 2)
                        review = app.review_text.get("1.0", "end")
                        self.assertIn("clk_out 接 top.u_tile.clk_core", review)
                        self.assertIn("reset_out 接 top.u_tile.rst_core", review)
                    return

                if scenario == "route2":
                    self.assertEqual(app.column_vars["RS_route2"].get(), "11")
                    sample = ROOT / "examples/specs_route2.csv"
                    app.excel_var.set(str(sample))
                    invoke_and_wait(app.validate_button, "VALID")
                    app.result_tree.selection_set("validation-0")
                    app._on_result_selected()
                    self.assertIn("只有全部名字都未命中", app.match_text.get("1.0", "end"))
                    invoke_and_wait(app.run_button, "PASS")
                    for n, name in enumerate(("AAAA_BBB_C2", "CTRL_RS_D0")):
                        app.result_tree.selection_set(f"row-{n}")
                        app._on_result_selected()
                        record = app._result_records[f"row-{n}"]
                        self.assertEqual(record["instance_match"]["mode"], "RS_route2")
                        self.assertIn(f"top.u_tile.{name}", app.match_text.get("1.0","end"))
                        self.assertIn("在 2 行相同", app.match_text.get("1.0","end"))
                    case = out / "partial.csv"
                    case.write_text(sample.read_text(encoding="utf-8").replace("AAAA_BBB_X", "AAAA_BBB_C1；AAAA_BBB_X", 1), encoding="utf-8")
                    app.excel_var.set(str(case))
                    invoke_and_wait(app.run_button, "FAIL")
                    app.result_tree.selection_set("row-0")
                    app._on_result_selected()
                    self.assertIn("部分 RS_inst 命中", app.match_text.get("1.0","end"))
                    record = app._result_records["row-0"]
                    self.assertEqual(record["instance_match"]["mode"], "RS_inst")
                    self.assertEqual([i["name"] for i in record["matched_instances"]], ["AAAA_BBB_C1"])
                    self.assertIsNone(record["step_check"]["effective_step"])
                    self.assertIn("不使用 RS_route 或 RS_route2 补选", app.review_text.get("1.0", "end"))
                    return

                if scenario == "match_review":
                    app.excel_var.set(str(ROOT / "examples/specs_route.csv"))
                    invoke_and_wait(app.validate_button, "VALID")
                    app.inspector_tabs.select(app.match_view)
                    root.update()
                    self.assertTrue(app.match_text.winfo_ismapped())
                    self.assertIn("尚未运行 RTL", app.match_text.get("1.0", "end"))
                    self.assertTrue(app.copy_match_button.instate(["disabled"]))
                    invoke_and_wait(app.run_button, "PASS")
                    app.result_tree.selection_set("row-0")
                    app._on_result_selected()
                    text = app.match_text.get("1.0", "end-1c")
                    self.assertIn("使用字段：RS_route", text)
                    self.assertIn("唯一最近：top.u_tile.CTRL_RS_D0（距离 1）", text)
                    self.assertIn("候选排名（显示 5/7）", text)
                    self.assertTrue(app.copy_match_button.instate(["!disabled"]))
                    with patch.object(root, "clipboard_clear"), patch.object(root, "clipboard_append") as copy:
                        app.copy_match_button.invoke()
                        copy.assert_called_once_with(text)
                    app.excel_var.set(str(ROOT / "examples/specs_multi_inst.xlsx"))
                    invoke_and_wait(app.run_button, "PASS")
                    app.result_tree.selection_set("row-2")
                    app._on_result_selected()
                    text = app.match_text.get("1.0", "end-1c")
                    self.assertIn('[1] RS_inst："CTRL_RS_D0"', text)
                    self.assertIn("完整名相同", text)
                    self.assertIn('[2] RS_inst："CTRL_RS_DO"', text)
                    self.assertIn("与前项重叠，不重复计拍", text)
                    self.assertIn("去重后选中 1 个", text)
                    self.assertNotIn("使用字段：RS_route", text)
                    tie = out / "tie.csv"
                    data = (ROOT / "examples/specs_route.csv").read_bytes()
                    tie.write_bytes(data.replace(b"CTRL_RS_DO", b"AAAA_BBB_C"))
                    app.excel_var.set(str(tie))
                    invoke_and_wait(app.run_button, "FAIL")
                    app.result_tree.selection_set("row-0")
                    app._on_result_selected()
                    root.update()
                    text = app.match_text.get("1.0", "end-1c")
                    self.assertIn("6 个并列（显示 6/6）", text)
                    self.assertIn("[并列未选] 距离 1  top.u_tile.AAAA_BBB_C5", text)
                    self.assertIn("有效拍数：未确定", text)
                    return

                if scenario == "continuation":
                    raw = "AAAA_BBB_C3；\\\nAAAA_BBB_C4"
                    book = out / "continuation.xlsx"
                    _xlsx(book, cell_overrides={"C2": f'<c r="C2" t="inlineStr"><is><t>{raw}</t></is></c>'})
                    app.excel_var.set(str(book))
                    invoke_and_wait(app.validate_button, "VALID")
                    spec = app._result_records["validation-0"]["spec"]
                    names = ["AAAA_BBB_C3", "AAAA_BBB_C4"]
                    self.assertEqual((spec["RS_inst"], spec["RS_inst_items"]), (raw, names))
                    self.assertIn("2 项", str(app.result_tree.set("validation-0", "rs_inst")))
                    invoke_and_wait(app.run_button, "PASS")
                    app.result_tree.selection_set("row-0")
                    app._on_result_selected()
                    record = json.loads(app.evidence_text.get("1.0", "end"))
                    match = record["instance_match"]
                    self.assertEqual((match["query"], match["matched_count"], record["step_check"]["effective_step"]),
                                     (raw, 2, 2))
                    self.assertEqual([item["matched_paths"] for item in match["items"]],
                                     [[f"top.u_tile.{name}"] for name in names])
                    self.assertTrue(all(item["rule"] == RS_INST_GROUP_RULE for item in match["items"]))
                    return

                if scenario == "inst_list_fuzzy":
                    book = out / "per_item.xlsx"
                    app.excel_var.set(str(book))
                    for first, rule, state, fuzzy_hits in (
                        ("AAAA_BBB_C1", RS_INST_GROUP_RULE, "PASS", 1),
                        ("AAAA_BBB_X1", RS_INST_FUZZY_RULE, "PASS", 2),
                        ("AAAA_BBB_X", RS_INST_FUZZY_RULE, "FAIL", 1),
                    ):
                        with self.subTest(first=first):
                            _xlsx(book, cell_overrides={
                                "C2": f'<c r="C2" t="inlineStr"><is><t>{first}\nAAAA_BBB_X2</t></is></c>',
                                "E2": '<c r="E2"><v>1</v></c>',
                                "J2": '<c r="J2" t="inlineStr"><is><t>CTRL_RS_D0</t></is></c>',
                            })
                            invoke_and_wait(app.run_button, state)
                            app.result_tree.selection_set("row-0")
                            app._on_result_selected()
                            display = str(app.result_tree.set("row-0", "rs_inst"))
                            self.assertIn(f"{fuzzy_hits} 项模糊命中", display)
                            review = app.review_text.get("1.0", "end")
                            self.assertIn(f"[1] {first}：", review)
                            self.assertIn("[2] AAAA_BBB_X2：模糊匹配", review)
                            self.assertIn("唯一最近，距离 1", review)
                            evidence = json.loads(app.evidence_text.get("1.0", "end"))
                            match = evidence["instance_match"]
                            self.assertTrue(match["route_ignored"])
                            a, b = match["items"]
                            self.assertEqual((a["rule"], b["rule"]), (rule, RS_INST_FUZZY_RULE))
                            self.assertEqual((b["selected"]["full_name"], b["candidate_count"], b["best_distance"]),
                                             ("top.u_tile.AAAA_BBB_C2", 7, 1))
                            if state == "PASS":
                                self.assertEqual(a["matched_paths"], ["top.u_tile.AAAA_BBB_C1"])
                                self.assertEqual(evidence["step_check"]["effective_step"], 1)
                            else:
                                self.assertIn("1 项未选中", display)
                                self.assertIn("并列候选（显示 5/6）：top.u_tile.AAAA_BBB_C0", review)
                                self.assertIsNone(a["selected"])
                                self.assertIsNone(evidence["step_check"]["effective_step"])
                    return

                if scenario == "formulas":
                    app.excel_var.set(str(ROOT / "examples/specs_formulas.xlsx"))
                    invoke_and_wait(app.validate_button, "VALID")
                    specs = [row["spec"] for row in app._result_records.values()]
                    self.assertEqual([s["step"] for s in specs], [5, 1])
                    self.assertEqual([s["RS_inst_formula_cell"] for s in specs], ["C2", "C3"])
                    app.result_tree.selection_set("validation-0")
                    app._on_result_selected()
                    self.assertIn('RS_inst 公式结果（RS_Check!C2，以保存值匹配）："AAAA_BBB"',
                                  app.match_text.get("1.0", "end-1c"))
                    self.assertEqual((specs[1]["RS_inst"], specs[1]["RS_route"]), ("", "CTRL_RS_DO"))
                    invoke_and_wait(app.run_button, "PARTIAL")
                    self.assertEqual(app.summary_pass_var.get(), "通过 1")
                    self.assertIn("未指定例化", str(app.result_tree.set("row-1", "rs_inst")))
                    app.result_tree.selection_set("row-1")
                    app._on_result_selected()
                    details = app.match_text.get("1.0", "end-1c")
                    self.assertIn('RS_inst 公式结果（RS_Check!C3，以保存值匹配）：""', details)
                    self.assertIn("不使用 RS_route 或 RS_route2", details)
                    self.assertNotIn("top.u_tile.CTRL_RS_D0", details)
                    self.assertEqual(app.log_text.get("1.0", "end").count("info[EXCEL_FORMULA_CACHE]"), 2)
                    bad = out / "uncalculated.xlsx"
                    _xlsx(bad, cell_overrides={"E2": '<c r="E2"><f>SUM(1,1)</f><v/></c>'})
                    app.excel_var.set(str(bad))
                    invoke_and_wait(app.run_button, "ERROR")
                    error.assert_called_once()
                    self.assertIn("RS_Check!E2 (step): formula has no cached result", error.call_args[0][1])
                    self.assertFalse(app.result_tree.get_children())
                    return

                if scenario in {"inst_list", "inst_multiline"}:
                    multiline = scenario == "inst_multiline"
                    book = "specs_multiline_inst.xlsx" if multiline else "specs_multi_inst.xlsx"
                    app.excel_var.set(str(ROOT / "examples" / book))
                    invoke_and_wait(app.validate_button, "VALID")
                    first = app._result_records["validation-0"]["spec"]
                    label = str(app.result_tree.set("validation-0", "rs_inst"))
                    self.assertEqual(first["RS_inst_items"], ["AAAA_BBB_C0", "AAAA_BBB_X2"])
                    self.assertIn("待 RTL 匹配", label)
                    self.assertIn("AAAA_BBB_C0；AAAA_BBB_X2", label)
                    self.assertNotIn("\n", label)
                    if multiline:
                        self.assertIn("\n", first["RS_inst"])
                    else:
                        self.assertIn("  ", first["RS_inst"])
                    invoke_and_wait(app.run_button, "PASS")
                    self.assertEqual(app.summary_pass_var.get(), "通过 3")
                    self.assertEqual([row["step_check"]["effective_step"] for row in app._result_records.values()],
                                     [1, 2, 1])
                    exact, fuzzy = app._result_records["row-0"]["instance_match"]["items"]
                    self.assertEqual(exact["matched_paths"], ["top.u_tile.AAAA_BBB_C0"])
                    self.assertEqual((fuzzy["query"], fuzzy["selected"]["full_name"], fuzzy["best_distance"]),
                                     ("AAAA_BBB_X2", "top.u_tile.AAAA_BBB_C2", 1))
                    app.result_tree.selection_set("row-2")
                    app._on_result_selected()
                    payload = json.loads(app.evidence_text.get("1.0", "end"))
                    self.assertIn("重叠 1 个", payload["例化匹配说明"])
                    self.assertEqual([item["added_count"] for item in payload["instance_match"]["items"]], [1, 0])

                    bad = out / "partial.xlsx"
                    separator = "_x000D_" if multiline else "、"
                    _xlsx(bad, cell_overrides={
                        "C2": f'<c r="C2" t="str"><f>K2</f><v>AAAA_BBB_C0{separator}AAAA_BBB_X</v></c>',
                        "E2": '<c r="E2"><v>1</v></c>',
                    })
                    app.excel_var.set(str(bad))
                    invoke_and_wait(app.run_button, "FAIL")
                    self.assertIn("1 项未选中", str(app.result_tree.set("row-0", "rs_inst")))
                    partial = app._result_records["row-0"]
                    self.assertEqual(partial["instance_match"]["items"][1]["tied_best_count"], 6)
                    self.assertIsNone(partial["step_check"]["effective_step"])
                    if not multiline:
                        joined = "AAAA_BBB_C3 \t,，、:：:: AAAA_BBB_X4"
                        _xlsx(bad, cell_overrides={"C2": f'<c r="C2" t="str"><f>K2</f><v>{joined}</v></c>'})
                        invoke_and_wait(app.run_button, "PASS")
                        record = app._result_records["row-0"]
                        self.assertEqual(record["spec"]["RS_inst_items"], ["AAAA_BBB_C3", "AAAA_BBB_X4"])
                        self.assertEqual(record["step_check"]["effective_step"], 2)
                        app.result_tree.selection_set("row-0")
                        app._on_result_selected()
                        review = app.match_text.get("1.0", "end-1c")
                        self.assertIn('[1] RS_inst："AAAA_BBB_C3"', review)
                        self.assertIn("RS_inst 公式结果（RS_Check!C2，以保存值匹配）", review)
                        self.assertIn("唯一最近：top.u_tile.AAAA_BBB_C4（距离 1）", review)
                        # Separators alone never activate a route.
                        route = "AAAA_BBB:C 4"
                        _xlsx(bad, cell_overrides={
                            "C2": '<c r="C2" t="inlineStr"><is><t>,，、::&#9;；</t></is></c>',
                            "J2": f'<c r="J2" t="inlineStr"><is><t>{route}</t></is></c>',
                            "E2": '<c r="E2"><v>1</v></c>',
                        })
                        invoke_and_wait(app.run_button, "PARTIAL")
                        chosen = app._result_records["row-0"]["instance_match"]
                        self.assertEqual((chosen["mode"], chosen["query"]), ("none", ""))
                    return

                if scenario == "inst_fallback":
                    source = ROOT / "examples" / "specs.csv"
                    for query, expected_mode in (("CTRL_RS_DO", "RS_inst"), ("AAAA_BBB_X", "RS_route")):
                        # Keep the reference name populated and deliberately different.
                        with source.open(encoding="utf-8-sig", newline="") as stream:
                            headers, group, control = list(csv.reader(stream))
                        control[2], control[9] = query, "CTRL_RS_D0"
                        case = out / "rs_inst.csv"
                        with case.open("w", encoding="utf-8", newline="") as stream:
                            csv.writer(stream).writerows((headers, group, control))
                        app.excel_var.set(str(case))
                        invoke_and_wait(app.run_button, "PASS")
                        self.assertEqual(app._result_records["row-0"]["step_check"]["effective_step"], 5)
                        app.result_tree.selection_set("row-1")
                        app._on_result_selected()
                        root.update()
                        match = json.loads(app.evidence_text.get("1.0", "end"))["instance_match"]
                        self.assertEqual((match["mode"], match["query"], match["candidate_count"]),
                                         (expected_mode, query if expected_mode == "RS_inst" else "CTRL_RS_D0", 7))
                        display = str(app.result_tree.set("row-1", "rs_inst"))
                        if expected_mode == "RS_inst":
                            self.assertTrue(match["route_ignored"])
                            self.assertEqual(match["selected"]["full_name"], "top.u_tile.CTRL_RS_D0")
                            self.assertIn("CTRL_RS_DO → CTRL_RS_D0", display)
                        else:
                            self.assertEqual(match["selected"]["name"], "CTRL_RS_D0")
                            self.assertEqual(match["primary_attempts"][0]["tied_best_count"], 6)
                            self.assertIn("RS_route", display)
                    return

                sample = ROOT / "examples" / "specs_route.csv"
                tie = out / "tie.csv"
                lines = sample.read_text(encoding="utf-8").splitlines()
                tie.write_text(lines[0] + "\n" + lines[1].replace("CTRL_RS_DO", "AAAA_BBB_C") + "\n", encoding="utf-8")

                for case, state in ((sample, "PASS"), (tie, "FAIL")):
                    app.excel_var.set(str(case))
                    invoke_and_wait(app.run_button, state)
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
        finally:
            if app.controller.is_running:
                app.controller.cancel()
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            root.destroy()
