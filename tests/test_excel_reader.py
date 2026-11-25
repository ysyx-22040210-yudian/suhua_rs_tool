from __future__ import annotations

import re
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
    cell_overrides: dict[str, str] | None = None,
    main_namespace: str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    document_relationship_namespace: str = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ),
) -> None:
    header_names = headers or tuple(COLUMNS)
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
        '<c r="E2"><v>2</v></c>'
        '<c r="F2" t="inlineStr"><is><t>clk_core</t></is></c>'
        '<c r="G2" t="inlineStr"><is><t>rst_core</t></is></c>'
        '<c r="H2" t="inlineStr"><is><t>crg_core</t></is></c>'
        '<c r="I2" t="inlineStr"><is><t>假门控</t></is></c>'
        "</row>",
    ]
    if cell_overrides:
        rows = [ET.fromstring(xml) for xml in cells]
        for address, xml in cell_overrides.items():
            row_number = re.fullmatch(r"[A-Z]+([0-9]+)", address).group(1)
            row = next(row for row in rows if row.get("r") == row_number)
            old = row.find(f"c[@r='{address}']")
            if old is not None:
                row.remove(old)
            row.append(ET.fromstring(xml))
        cells = [ET.tostring(row, encoding="unicode") for row in rows]
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
        self.assertEqual(rows[0].clk, "clk_core")
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
                    "rs_route",
                )
            )

        self.assertEqual([values(row) for row in xlsx_rows], [values(row) for row in csv_rows])
        self.assertEqual([row.rs_route for row in xlsx_rows], ["NA", "CTRL_RS_DO"])
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

        step_validations = [v for v in validations if "E2:E3" in v[0]]
        self.assertEqual(len(step_validations), 1)
        self.assertEqual(step_validations[0][1], "custom")
        self.assertIn('EXACT(TRIM(E2&""),"NA")', step_validations[0][3])
        self.assertIn('E2>=0', step_validations[0][3])
        self.assertIn('INT(E2)=E2', step_validations[0][3])
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

        self.assertIn("独立追两条路径", guide_values["F13"])
        self.assertIn("证据不足为警告", guide_values["G13"])
        self.assertIn("线名不同为错误", guide_values["G13"])
        self.assertEqual(guide_values["C14"], "可选 / 文本")
        self.assertEqual(
            guide_values["D14"], "RS_CRG_EN 门控参数的兼容标签字段"
        )
        self.assertEqual(guide_values["E14"], "假门控 / NA")
        self.assertIn("留空或填 NA 跳过门控检查", guide_values["F14"])
        self.assertIn("RS_CRG_EN", guide_values["F14"])
        self.assertIn("规则为 false 时本列不参与判定", guide_values["F14"])
        self.assertIn("空值警告；大写 NA 不报警", guide_values["G14"])
        self.assertIn("na、N/A 不是豁免值", guide_values["G14"])
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
                "例化参考名",
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

    def test_cached_formula_values_follow_column_mapping_not_headers(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "formula.xlsm"
            _xlsx(path, cell_overrides={
                "E2": '<c r="E2" t="e"><f>1/0</f><v>#DIV/0!</v></c>',  # now unmapped
                "AA2": '<c r="AA2"><f>SUM(1,1)</f><v>2E+00</v></c>',
                "I2": '<c r="I2"><f>0</f><v>0</v></c>',
                "C2": '<c r="C2" t="str"><f>TEXT(5,"000")</f><v>005</v></c>',
                "D2": '<c r="D2" t="str"><f t="shared" si="0"/><v>tile</v></c>',
                "H2": '<c r="H2" t="str"><f t="array" ref="H2">A1</f><v>src</v></c>',
            })
            config = ExcelConfig(sheet=1, columns=dict(COLUMNS, step=27))
            messages = []
            row, = read_spec_rows(path, config, {"tile": "top.u"}, {"src": "top.crg"},
                                  on_notice=messages.append)
        self.assertEqual((row.step, row.rs_cfg_en, row.rs_inst), (2, "0", "005"))
        self.assertEqual((row.position_alias, row.position), ("tile", "top.u"))
        self.assertEqual((row.crg_source_alias, row.crg_source), ("src", "top.crg"))
        self.assertEqual(len(messages), 1)
        self.assertIn("5 formula cell(s)", messages[0])

    def test_cached_empty_string_allows_route_but_missing_cache_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "formula.xlsx"
            config = ExcelConfig(sheet=1, columns=dict(COLUMNS, RS_route=10))
            cells = {
                "C2": '<c r="C2" t="str"><f>IF(1,"","x")</f><v/></c>',
                "J2": '<c r="J2" t="str"><f>A1</f><v>bus:core</v></c>',
                "I2": '<c r="I2" t="str"><f>IF(1,"NA","")</f><v>NA</v></c>',
            }
            _xlsx(path, cell_overrides=cells)
            row, = read_spec_rows(path, config)
            self.assertEqual((row.rs_inst, row.rs_route, row.rs_cfg_en), ("", "bus:core", "NA"))
            for cache in ('t="str"><f>IF(1,"","x")</f>', 't="n"><f>0</f><v/>'):
                _xlsx(path, cell_overrides=dict(cells, C2=f'<c r="C2" {cache}</c>'))
                with self.subTest(cache=cache), self.assertRaisesRegex(
                    WorkbookError, r"RS_Check!C2 \(RS_inst\).*no cached result.*Recalculate"
                ):
                    read_spec_rows(path, config)

    def test_cache_errors_and_bad_steps_are_not_silently_coerced(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad.xlsx"
            for kind, cached, message in (
                ("n", "", "no cached result"), ("n", "<v/>", "no cached result"),
                ("n", "<v>nan</v>", "invalid cached number"), ("n", "<v>1.5</v>", "non-negative integer"),
                ("n", "<v>-1</v>", "non-negative integer"), ("n", "<v>1E9999999</v>", "non-negative integer"),
                ("b", "<v>0</v>", "non-negative integer"), ("str", "<v>1e0</v>", "non-negative integer"),
                ("e", "<v>#REF!</v>", "formula returned Excel error '#REF!'"),
                ("s", "<v>-1</v>", "invalid shared-string index"),
            ):
                _xlsx(path, cell_overrides={"E2": f'<c r="E2" t="{kind}"><f>A1</f>{cached}</c>'})
                with self.subTest(kind=kind, cached=cached), self.assertRaisesRegex(WorkbookError, message):
                    read_spec_rows(path, self.config)
            _xlsx(path, cell_overrides={"I2": '<c r="I2" t="e"><f>NA()</f><v>#N/A</v></c>'})
            with self.assertRaisesRegex(WorkbookError, r"RS_Check!I2 \(RS_CFG_EN\).*'#N/A'"):
                read_spec_rows(path, self.config)
            _xlsx(path, cell_overrides={"E2": '<c r="E2"><f>0</f><v>0</v></c>'})
            self.assertEqual(read_spec_rows(path, self.config)[0].step, 0)

    def test_only_read_formulas_in_selected_mapped_rows(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "headers.xlsx"
            _xlsx(path, cell_overrides={
                "A1": '<c r="A1" t="e"><f>NA()</f><v>#N/A</v></c>',
                "K2": '<c r="K2"><f>A2</f></c>',
            })
            messages = []
            self.assertEqual(read_spec_rows(path, self.config, on_notice=messages.append)[0].step, 2)
            self.assertEqual(messages, [])
            strict = ExcelConfig(sheet=1, columns=COLUMNS, validate_headers=True)
            with self.assertRaisesRegex(WorkbookError, r"RS_Check!A1 \(Intf_type\).*#N/A"):
                read_spec_rows(path, strict)
            _xlsx(path, cell_overrides={"A1": '<c r="A1" t="str"><f>B9</f><v>Intf_type</v></c>'})
            self.assertEqual(read_spec_rows(path, strict)[0].intf_type, "OUT_IF")

    def test_delimited_files_keep_equals_text_literal(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            for ext, sep in (("csv", ","), ("tsv", "\t")):
                path = Path(name) / f"literal.{ext}"
                for step in ("2", "=1+1"):
                    record = ["OUT_IF", "rs_pipe", "=B5", "top.u", step, "clk", "rst", "crg", ""]
                    path.write_text(sep.join(COLUMNS) + "\n" + sep.join(record) + "\n", encoding="utf-8")
                    with self.subTest(ext=ext, step=step):
                        if step == "2":
                            messages = []
                            row, = read_spec_rows(path, self.config, on_notice=messages.append)
                            self.assertEqual(row.rs_inst, "=B5")
                            self.assertEqual(messages, [])
                        else:
                            with self.assertRaisesRegex(WorkbookError, "non-negative integer"):
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

    def test_partial_row_records_gate_fields_without_a_workbook_error(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "partial.csv"
            path.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "OUT_IF,rs_pipe,,,,,,,假门控\n",
                encoding="utf-8",
            )
            config = ExcelConfig(sheet=1, columns=COLUMNS)
            skipped = []
            self.assertEqual(read_spec_rows(path, config, on_skip=skipped.append), [])
            self.assertEqual(skipped[0].as_dict(), {"sheet": "CSV", "row": 2,
                                                    "fields": {"position": "blank", "step": "blank"}})


if __name__ == "__main__":
    unittest.main()
