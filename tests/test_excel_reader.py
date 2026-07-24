from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

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
    formula_step: bool = False,
    formula_rs_cfg_en: bool = False,
    main_namespace: str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    document_relationship_namespace: str = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ),
) -> None:
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
            for letter, name in zip("ABCDEFGHI", COLUMNS)
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
        self.assertEqual(rows[1].rs_inst, "CTRL_RS")
        self.assertEqual(rows[0].rs_cfg_en, "假门控")

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
        config = load_config(ROOT / "config" / "rscheck.example.json").excel
        xlsx_rows = read_spec_rows(ROOT / "examples" / "RS_Check_Excel_Template.xlsx", config)
        csv_rows = read_spec_rows(ROOT / "examples" / "specs.csv", config)

        def values(row: object) -> tuple[object, ...]:
            return tuple(
                getattr(row, field)
                for field in (
                    "intf_type",
                    "rs_module",
                    "rs_inst",
                    "position",
                    "step",
                    "clk",
                    "rst",
                    "crg_source",
                    "rs_cfg_en",
                )
            )

        self.assertEqual([values(row) for row in xlsx_rows], [values(row) for row in csv_rows])

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
                "unused,RS_inst,clk,Intf_type,CRG_source,step,position,rst,RS_module,RS_CFG_EN,notes\n"
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
