from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from rscheck.config import load_config
from rscheck.excel_reader import column_letters_to_index, read_spec_rows
from rscheck.model import ExcelConfig, WorkbookError


ROOT = Path(__file__).resolve().parents[1]
COLUMNS = {
    "Intf_type": 1,
    "RS_module": 2,
    "RS_inst": 3,
    "position": 4,
    "step": 5,
    "clk": 6,
    "rst": 7,
    "CRG_source": 8,
    "RS_CFG_EN": 9,
}


def _xlsx(
    path: Path,
    *,
    headers: tuple[str, ...] | None = None,
    formula_step: bool = False,
    formula_rs_cfg_en: bool = False,
    main_namespace: str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    document_relationship_namespace: str = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ),
) -> None:
    header_names = headers or tuple(COLUMNS)
    step_cell = "<f>1+1</f><v>2</v>" if formula_step else "<v>2</v>"
    rs_cfg_en_cell = (
        '<c r="I2"><f>0</f><v>0</v></c>'
        if formula_rs_cfg_en
        else '<c r="I2" t="inlineStr"><is><t>假门控</t></is></c>'
    )
    cells = [
        "<row r=\"1\">"
        + "".join(
            f'<c r="{letter}1" t="inlineStr"><is><t>{name}</t></is></c>'
            for letter, name in zip("ABCDEFGHI", header_names)
        )
        + "</row>",
        "<row r=\"2\">"
        '<c r="A2" t="inlineStr"><is><t>OUT_IF</t></is></c>'
        '<c r="B2" t="inlineStr"><is><t>rs_pipe</t></is></c>'
        '<c r="C2" t="inlineStr"><is><t>AAAA_BBB</t></is></c>'
        '<c r="D2" t="inlineStr"><is><t>top.u_tile</t></is></c>'
        f'<c r="E2">{step_cell}</c>'
        '<c r="F2" t="inlineStr"><is><t>clk_rs</t></is></c>'
        '<c r="G2" t="inlineStr"><is><t>rst_n</t></is></c>'
        '<c r="H2" t="inlineStr"><is><t>crg_core</t></is></c>'
        f"{rs_cfg_en_cell}"
        "</row>",
    ]
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{main_namespace}">'
        f"<sheetData>{''.join(cells)}</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<workbook xmlns="{main_namespace}" '
            f'xmlns:r="{document_relationship_namespace}">'
            '<sheets><sheet name="RS_Check" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def _xlsx_with_shared_strings(path: Path, *, corrupt_shared_strings: bool = False) -> None:
    strings = list(COLUMNS) + [
        "OUT_IF",
        "rs_pipe",
        "AAAA_BBB",
        "top.u_tile",
        "clk_rs",
        "rst_n",
        "crg_core",
        "假门控",
    ]
    header = "".join(
        f'<c r="{letter}1" t="s"><v>{index}</v></c>'
        for index, letter in enumerate("ABCDEFGHI")
    )
    data_indices = [9, 10, 11, 12, None, 13, 14, 15, 16]
    data = "".join(
        f'<c r="{letter}2"><v>2</v></c>'
        if index is None
        else f'<c r="{letter}2" t="s"><v>{index}</v></c>'
        for letter, index in zip("ABCDEFGHI", data_indices)
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="1">{header}</row><row r="2">{data}</row></sheetData>'
        "</worksheet>"
    )
    shared_items = []
    for index, value in enumerate(strings):
        phonetic = "<rPh><t>IGNORED</t></rPh>" if index == 9 else ""
        shared_items.append(f"<si><t>{value}</t>{phonetic}</si>")
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(shared_items)
        + "</sst>"
    )
    if corrupt_shared_strings:
        shared = "<sst><si>"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="RS_Check" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


class ExcelReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ExcelConfig(sheet="RS_Check", columns=COLUMNS)

    def test_csv_is_parsed(self) -> None:
        rows = read_spec_rows(ROOT / "tests" / "fixtures" / "specs.csv", self.config)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].step, 5)
        self.assertEqual(rows[1].rs_inst, "CTRL_RS_D0")
        self.assertEqual(rows[0].rs_cfg_en, "假门控")

    def test_rs_cfg_en_na_is_trimmed_during_csv_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "specs.csv"
            path.write_text(
                (
                    "Intf_type,RS_module,RS_inst,position,step,clk,rst,"
                    "CRG_source,RS_CFG_EN\n"
                    "OUT_IF,rs_pipe,AAAA_BBB,top.u_tile,1,clk,rst_n,crg,  NA  \n"
                ),
                encoding="utf-8",
            )
            rows = read_spec_rows(path, self.config)

        self.assertEqual(rows[0].rs_cfg_en, "NA")

    def test_columns_beyond_z_are_supported(self) -> None:
        self.assertEqual(column_letters_to_index("AA"), 27)
        self.assertEqual(column_letters_to_index("XFD"), 16384)

    def test_minimal_xlsx_is_parsed_without_third_party_packages(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "spec.xlsx"
            _xlsx(path)
            rows = read_spec_rows(path, self.config)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].clk, "clk_rs")
        self.assertEqual(rows[0].rs_cfg_en, "假门控")

    def test_arbitrary_xlsx_headers_use_mapped_column_positions(self) -> None:
        headers = (
            "接口分类",
            "模块类型",
            "实例组",
            "位置简称",
            "有效拍数",
            "时钟连接",
            "复位连接",
            "时钟源模块",
            "假门控标记",
        )
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "arbitrary_headers.xlsx"
            _xlsx(path, headers=headers)
            rows = read_spec_rows(path, self.config)
            strict = ExcelConfig(
                sheet="RS_Check",
                validate_headers=True,
                columns=COLUMNS,
            )
            with self.assertRaisesRegex(WorkbookError, "header validation failed"):
                read_spec_rows(path, strict)

        self.assertEqual(rows[0].intf_type, "OUT_IF")
        self.assertEqual(rows[0].rs_module, "rs_pipe")
        self.assertEqual(rows[0].rs_inst, "AAAA_BBB")

    def test_normal_excel_shared_strings_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "shared.xlsx"
            _xlsx_with_shared_strings(path)
            rows = read_spec_rows(path, self.config)
        self.assertEqual(rows[0].rs_module, "rs_pipe")
        self.assertEqual(rows[0].step, 2)
        self.assertEqual(rows[0].intf_type, "OUT_IF")
        self.assertEqual(rows[0].rs_cfg_en, "假门控")

    def test_repository_excel_template_matches_example_csv(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")
        xlsx_rows = read_spec_rows(
            ROOT / "examples" / "RS_Check_Excel_Template.xlsx",
            config.excel,
            config.position_mappings,
            config.crg_source_mappings,
        )
        csv_rows = read_spec_rows(
            ROOT / "examples" / "specs.csv",
            config.excel,
            config.position_mappings,
            config.crg_source_mappings,
        )

        def values(row: object) -> tuple[object, ...]:
            return tuple(
                getattr(row, field)
                for field in (
                    "intf_type",
                    "rs_module",
                    "rs_inst",
                    "position",
                    "position_alias",
                    "step",
                    "clk",
                    "rst",
                    "crg_source",
                    "crg_source_alias",
                    "rs_cfg_en",
                )
            )

        self.assertEqual([values(row) for row in xlsx_rows], [values(row) for row in csv_rows])
        self.assertTrue(
            all(
                row.position == "top.u_tile" and row.position_alias == "tile_core"
                for row in xlsx_rows
            )
        )
        self.assertEqual(
            [(row.crg_source, row.crg_source_alias) for row in xlsx_rows],
            [
                ("top.u_tile.u_crg", "crg_core"),
                ("top.u_tile.u_aux_crg", "crg_aux"),
            ],
        )

    def test_repository_excel_template_rs_cfg_en_guidance_and_validation(self) -> None:
        template = ROOT / "examples" / "RS_Check_Excel_Template.xlsx"
        with zipfile.ZipFile(template) as archive:
            spec_sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            guide_sheet = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))

        validations = []
        for element in spec_sheet.iter():
            if element.tag.rsplit("}", 1)[-1] != "dataValidation":
                continue
            formulas = {
                child.tag.rsplit("}", 1)[-1]: child.text
                for child in element
                if child.tag.rsplit("}", 1)[-1] in {"formula1", "formula2"}
            }
            validations.append(
                (
                    tuple(
                        reference.replace("$", "")
                        for reference in element.attrib.get("sqref", "").split()
                    ),
                    element.attrib.get("type"),
                    element.attrib.get("operator"),
                    formulas.get("formula1"),
                    formulas.get("formula2"),
                )
            )

        self.assertIn(
            (("E2:E3",), "whole", "between", "0", "2147483647"), validations
        )
        self.assertFalse(
            any(
                "I2:I3" in references
                for references, _type, _operator, _formula1, _formula2 in validations
            )
        )

        guide_values = {}
        for cell in guide_sheet.iter():
            if cell.tag.rsplit("}", 1)[-1] != "c":
                continue
            reference = cell.attrib.get("r", "")
            if reference not in {
                "F13",
                "G13",
                "C14",
                "D14",
                "E14",
                "F14",
                "G14",
                "A15",
                "A17",
            }:
                continue
            guide_values[reference] = "".join(
                (descendant.text or "")
                for descendant in cell.iter()
                if descendant.tag.rsplit("}", 1)[-1] in {"t", "v"}
            )

        self.assertIn("config.crg_source_mappings", guide_values["F13"])
        self.assertIn("完整 RTL 路径", guide_values["F13"])
        self.assertIn("RS clk formal", guide_values["F13"])
        self.assertIn("所有 input 有界递归追踪", guide_values["F13"])
        self.assertIn("精确排除 clk、rst_n", guide_values["F13"])
        self.assertIn("完整实例层次路径", guide_values["F13"])
        self.assertIn("GUI CRG Source映射库", guide_values["G13"])
        self.assertIn("未命中时按完整路径使用", guide_values["G13"])
        self.assertIn("rtl.crg_trace_max_depth", guide_values["G13"])
        self.assertIn("产生 CRG warning", guide_values["G13"])
        self.assertIn("不改变该行其他检查的 PASS/FAIL", guide_values["G13"])
        self.assertEqual(guide_values["C14"], "可选 / 文本")
        self.assertEqual(
            guide_values["D14"], "RS_CRG_EN 门控参数的兼容标签字段"
        )
        self.assertEqual(guide_values["E14"], "假门控 / NA")
        self.assertIn("精确填写 NA 时跳过该行全部", guide_values["F14"])
        self.assertIn("RS_CFG_EN/RS_CRG_EN 检查", guide_values["F14"])
        self.assertIn("规则为 false 时本列不参与判定", guide_values["F14"])
        self.assertIn("仅精确大写 NA（首尾空白忽略）会跳过", guide_values["G14"])
        self.assertIn("na、N/A 不会", guide_values["G14"])
        self.assertIn("step、clk、rst 等其他检查仍执行", guide_values["G14"])
        self.assertIn("Position/CRG Source映射", guide_values["A15"])
        self.assertIn("position 和 CRG_source 均可填简称", guide_values["A17"])
        self.assertIn("config.position_mappings", guide_values["A17"])
        self.assertIn("config.crg_source_mappings", guide_values["A17"])

    def test_repository_excel_table_metadata_matches_visible_headers(self) -> None:
        expected = {
            "xl/tables/table1.xml": [
                "接口分类",
                "模块类型",
                "实例组",
                "位置简称",
                "有效拍数",
                "时钟连接",
                "复位连接",
                "时钟源模块",
                "假门控标记",
            ],
            "xl/tables/table2.xml": [
                "默认列",
                "工具内部属性",
                "必填 / 类型",
                "含义",
                "示例",
                "RTL 检查方式",
                "填写注意",
            ],
        }
        with zipfile.ZipFile(ROOT / "examples" / "RS_Check_Excel_Template.xlsx") as archive:
            for entry_name, expected_columns in expected.items():
                with self.subTest(entry=entry_name), archive.open(entry_name) as stream:
                    table = ET.parse(stream).getroot()
                    actual_columns = [
                        element.attrib["name"]
                        for element in table.iter()
                        if element.tag.rsplit("}", 1)[-1] == "tableColumn"
                    ]
                self.assertEqual(actual_columns, expected_columns)

    def test_formula_in_mapped_cell_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "formula.xlsx"
            _xlsx(path, formula_step=True)
            with self.assertRaisesRegex(WorkbookError, "formula cells"):
                read_spec_rows(path, self.config)

    def test_formula_in_rs_cfg_en_cell_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "formula_rs_cfg_en.xlsx"
            _xlsx(path, formula_rs_cfg_en=True)
            with self.assertRaisesRegex(WorkbookError, "formula cells"):
                read_spec_rows(path, self.config)

    def test_strict_ooxml_namespace_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "strict.xlsx"
            _xlsx(
                path,
                main_namespace="http://purl.oclc.org/ooxml/spreadsheetml/main",
                document_relationship_namespace=(
                    "http://purl.oclc.org/ooxml/officeDocument/relationships"
                ),
            )
            rows = read_spec_rows(path, self.config)
        self.assertEqual(rows[0].rs_inst, "AAAA_BBB")

    def test_malformed_shared_strings_is_a_controlled_workbook_error(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad_shared.xlsx"
            _xlsx_with_shared_strings(path, corrupt_shared_strings=True)
            with self.assertRaisesRegex(WorkbookError, "sharedStrings"):
                read_spec_rows(path, self.config)

    def test_columns_can_be_arbitrarily_reordered_with_extra_columns(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "shuffled.csv"
            path.write_text(
                "备注甲,实例列,时钟列,接口列,时钟源列,拍数列,位置列,复位列,模块列,门控列,备注乙\n"
                "x,PIPE_X,clk_i,IN_IF,my_crg,1,top.u,rst_n,my_pipe, 假门控 ,n\n",
                encoding="utf-8",
            )
            config = ExcelConfig(
                sheet=1,
                columns={
                    "Intf_type": 4,
                    "RS_module": 9,
                    "RS_inst": 2,
                    "position": 7,
                    "step": 6,
                    "clk": 3,
                    "rst": 8,
                    "CRG_source": 5,
                    "RS_CFG_EN": 10,
                },
            )
            rows = read_spec_rows(path, config)
        self.assertEqual(rows[0].rs_inst, "PIPE_X")
        self.assertEqual(rows[0].rs_module, "my_pipe")
        self.assertEqual(rows[0].rs_cfg_en, "假门控")

    def test_tsv_uses_tabs_even_when_extra_text_contains_commas(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "spec.tsv"
            path.write_text(
                "Intf_type\tRS_module\tRS_inst\tposition\tstep\tclk\trst\tCRG_source\tRS_CFG_EN\tnote\n"
                "OUT\tpipe\tPFX\ttop.u\t1\tclk\trst\tcrg\t\tone,two,three\n",
                encoding="utf-8",
            )
            rows = read_spec_rows(path, ExcelConfig(sheet=1, columns=COLUMNS))
        self.assertEqual(rows[0].rs_inst, "PFX")
        self.assertEqual(rows[0].rs_cfg_en, "")

    def test_duplicate_group_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "duplicate.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,top.u,1,clk,rst,crg,\n"
                "B,pipe,PFX,top.u,1,clk,rst,crg,\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkbookError, "duplicate group"):
                read_spec_rows(path, ExcelConfig(sheet=1, columns=COLUMNS))

    def test_position_alias_is_resolved_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "alias.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,tile0,1,clk,rst,crg,\n",
                encoding="utf-8",
            )
            rows = read_spec_rows(
                path,
                ExcelConfig(sheet=1, columns=COLUMNS),
                {"tile0": "top.cluster.u_tile0"},
            )
        self.assertEqual(rows[0].position, "top.cluster.u_tile0")
        self.assertEqual(rows[0].position_alias, "tile0")
        self.assertEqual(rows[0].as_dict()["position"], "top.cluster.u_tile0")
        self.assertEqual(rows[0].as_dict()["position_alias"], "tile0")

    def test_unmapped_position_passes_through_as_full_path(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "full_path.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,top.cluster.u_tile0,1,clk,rst,crg,\n",
                encoding="utf-8",
            )
            rows = read_spec_rows(
                path,
                ExcelConfig(sheet=1, columns=COLUMNS),
                {"another_alias": "top.other"},
            )
        self.assertEqual(rows[0].position, "top.cluster.u_tile0")
        self.assertEqual(rows[0].position_alias, "")

    def test_position_mapping_is_exact_and_one_level(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "one_level.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,tile0,1,clk,rst,crg,\n",
                encoding="utf-8",
            )
            rows = read_spec_rows(
                path,
                ExcelConfig(sheet=1, columns=COLUMNS),
                {"tile0": "tile1", "tile1": "top.cluster.u_tile1"},
            )
        self.assertEqual(rows[0].position, "tile1")
        self.assertEqual(rows[0].position_alias, "tile0")

    def test_crg_source_alias_is_resolved_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "crg_alias.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,top.u,1,clk,rst,.core_crg.,\n",
                encoding="utf-8",
            )
            rows = read_spec_rows(
                path,
                ExcelConfig(sheet=1, columns=COLUMNS),
                {},
                {"core_crg": ".tb_top.dut.u_crg."},
            )
        self.assertEqual(rows[0].crg_source, "tb_top.dut.u_crg")
        self.assertEqual(rows[0].crg_source_alias, "core_crg")
        self.assertEqual(rows[0].as_dict()["CRG_source"], "tb_top.dut.u_crg")
        self.assertEqual(rows[0].as_dict()["crg_source_alias"], "core_crg")

    def test_unmapped_crg_source_passes_through_and_is_case_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "crg_full_path.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,top.u,1,clk,rst,.core_crg.,\n",
                encoding="utf-8",
            )
            rows = read_spec_rows(
                path,
                ExcelConfig(sheet=1, columns=COLUMNS),
                {},
                {"CORE_CRG": "tb_top.dut.u_crg"},
            )
        self.assertEqual(rows[0].crg_source, "core_crg")
        self.assertEqual(rows[0].crg_source_alias, "")

    def test_crg_source_mapping_is_exact_and_one_level(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "crg_one_level.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,top.u,1,clk,rst,crg0,\n",
                encoding="utf-8",
            )
            rows = read_spec_rows(
                path,
                ExcelConfig(sheet=1, columns=COLUMNS),
                {},
                {"crg0": "crg1", "crg1": "tb_top.dut.u_crg"},
            )
        self.assertEqual(rows[0].crg_source, "crg1")
        self.assertEqual(rows[0].crg_source_alias, "crg0")

    def test_crg_source_only_dots_is_rejected_after_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "empty_crg.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,top.u,1,clk,rst,...,\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkbookError, "CRG_source cannot be empty"):
                read_spec_rows(path, ExcelConfig(sheet=1, columns=COLUMNS))

    def test_duplicate_group_after_position_resolution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "resolved_duplicate.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,tile0,1,clk,rst,crg,\n"
                "B,pipe,PFX,top.cluster.u_tile0,1,clk,rst,crg,\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                WorkbookError, "duplicate group .* after position resolution"
            ):
                read_spec_rows(
                    path,
                    ExcelConfig(sheet=1, columns=COLUMNS),
                    {"tile0": "top.cluster.u_tile0"},
                )

    def test_non_integral_and_scientific_steps_are_rejected(self) -> None:
        for value in ("-1", "2.5", "2e0"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                path = Path(name) / "bad_step.csv"
                path.write_text(
                    "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                    f"A,pipe,PFX,top.u,{value},clk,rst,crg,\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(WorkbookError, "non-negative integer"):
                    read_spec_rows(path, ExcelConfig(sheet=1, columns=COLUMNS))

    def test_zero_step_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "zero_step.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "A,pipe,PFX,top.u,0,clk,rst,crg,\n",
                encoding="utf-8",
            )
            rows = read_spec_rows(path, ExcelConfig(sheet=1, columns=COLUMNS))
        self.assertEqual(rows[0].step, 0)

    def test_partial_row_reports_all_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "partial.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "OUT_IF,rs_pipe,,,,,,,假门控\n",
                encoding="utf-8",
            )
            config = ExcelConfig(sheet=1, columns=COLUMNS)
            with self.assertRaisesRegex(WorkbookError, "blank required fields"):
                read_spec_rows(path, config)


if __name__ == "__main__":
    unittest.main()
