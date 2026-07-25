from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator, Mapping
from xml.etree import ElementTree as ET

from .model import (
    FIELD_NAMES,
    REQUIRED_ROW_FIELDS,
    ExcelConfig,
    SpecRow,
    WorkbookError,
)


_CELL_REF_RE = re.compile(r"^([A-Z]+)([0-9]+)$")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_child(element: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in element if _local_name(child.tag) == name), None)


def column_letters_to_index(letters: str) -> int:
    result = 0
    for char in letters:
        if char < "A" or char > "Z":
            raise WorkbookError(f"invalid Excel column reference: {letters}")
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def _cell_column(reference: str) -> int:
    match = _CELL_REF_RE.match(reference.upper())
    if not match:
        raise WorkbookError(f"invalid Excel cell reference: {reference}")
    return column_letters_to_index(match.group(1))


def _normalise_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_step(value: str, row_number: int) -> int:
    if not re.fullmatch(r"[0-9]+(?:\.0+)?", value):
        raise WorkbookError(f"row {row_number}: step must be a non-negative integer, got {value!r}")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise WorkbookError(f"row {row_number}: step must be a non-negative integer, got {value!r}") from exc
    if not number.is_finite() or number != number.to_integral_value() or number < 0:
        raise WorkbookError(f"row {row_number}: step must be a non-negative integer, got {value!r}")
    return int(number)


def _validate_headers(rows: Iterable[tuple[int, Mapping[int, str]]], config: ExcelConfig) -> None:
    for row_number, cells in rows:
        if row_number != config.header_row:
            continue
        mismatches = []
        for field_name, column in config.columns.items():
            actual = _normalise_text(cells.get(column, ""))
            if actual != field_name:
                mismatches.append(f"column {column}: expected {field_name!r}, got {actual!r}")
        if mismatches:
            raise WorkbookError(
                f"header validation failed at row {config.header_row}: " + "; ".join(mismatches)
            )
        return
    raise WorkbookError(f"header row {config.header_row} was not found")


def _rich_text(container: ET.Element) -> str:
    parts: list[str] = []
    for child in container:
        if _local_name(child.tag) == "t":
            parts.append(child.text or "")
        elif _local_name(child.tag) == "r":
            text = _first_child(child, "t")
            if text is not None:
                parts.append(text.text or "")
    return "".join(parts)


def _rows_to_specs(
    path: Path,
    sheet_name: str,
    rows: Iterable[tuple[int, Mapping[int, str]]],
    config: ExcelConfig,
    position_mappings: Mapping[str, str],
) -> list[SpecRow]:
    materialised = list(rows)
    if config.validate_headers:
        _validate_headers(materialised, config)

    specs: list[SpecRow] = []
    errors: list[str] = []
    seen: dict[tuple[str, str], tuple[int, str]] = {}
    for row_number, cells in materialised:
        if row_number < config.data_start_row:
            continue
        values = {
            field_name: _normalise_text(cells.get(column, ""))
            for field_name, column in config.columns.items()
        }
        if not any(values.values()):
            continue
        missing = [
            field_name for field_name in REQUIRED_ROW_FIELDS if not values[field_name]
        ]
        if missing:
            errors.append(f"row {row_number}: blank required fields: {', '.join(missing)}")
            continue
        try:
            step = _parse_step(values["step"], row_number)
        except WorkbookError as exc:
            errors.append(str(exc))
            continue
        position_input = values["position"].strip(".")
        if position_input in position_mappings:
            position = position_mappings[position_input].strip(".")
            position_alias = position_input
        else:
            position = position_input
            position_alias = ""

        spec = SpecRow(
            source=path,
            sheet=sheet_name,
            row_number=row_number,
            intf_type=values["Intf_type"],
            rs_module=values["RS_module"],
            rs_inst=values["RS_inst"],
            position=position,
            step=step,
            clk=values["clk"],
            rst=values["rst"],
            crg_source=values["CRG_source"],
            rs_cfg_en=values["RS_CFG_EN"],
            position_alias=position_alias,
        )
        if not spec.position:
            errors.append(f"row {row_number}: position cannot be empty")
            continue
        if spec.key in seen:
            first_row, first_position_input = seen[spec.key]
            errors.append(
                f"row {row_number}: duplicate group ({spec.position}, {spec.rs_inst}) "
                f"after position resolution from {position_input!r}; first defined "
                f"from {first_position_input!r} at row {first_row}"
            )
            continue
        seen[spec.key] = (row_number, position_input)
        specs.append(spec)

    if errors:
        raise WorkbookError("invalid workbook data:\n  " + "\n  ".join(errors))
    if not specs:
        raise WorkbookError("no specification rows were found")
    return specs


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    strings: list[str] = []
    try:
        with archive.open(name) as stream:
            for _, element in ET.iterparse(stream, events=("end",)):
                if _local_name(element.tag) == "si":
                    strings.append(_rich_text(element))
                    element.clear()
    except ET.ParseError as exc:
        raise WorkbookError(f"invalid XLSX sharedStrings XML: {exc}") from exc
    return strings


def _sheet_target(archive: zipfile.ZipFile, selector: str | int) -> tuple[str, str]:
    try:
        with archive.open("xl/workbook.xml") as stream:
            workbook = ET.parse(stream).getroot()
        with archive.open("xl/_rels/workbook.xml.rels") as stream:
            relationships = ET.parse(stream).getroot()
    except (KeyError, ET.ParseError) as exc:
        raise WorkbookError("invalid XLSX: workbook metadata is missing or malformed") from exc

    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships
        if _local_name(rel.tag) == "Relationship"
        if "Id" in rel.attrib and "Target" in rel.attrib
    }
    sheets = []
    for sheet in workbook.iter():
        if _local_name(sheet.tag) != "sheet":
            continue
        name = sheet.attrib.get("name", "")
        rel_id = next(
            (
                value
                for attribute, value in sheet.attrib.items()
                if attribute.startswith("{") and _local_name(attribute) == "id"
            ),
            "",
        )
        if rel_id in rel_targets:
            target = rel_targets[rel_id].replace("\\", "/")
            if target.startswith("/"):
                target = target.lstrip("/")
            else:
                target = posixpath.normpath(posixpath.join("xl", target))
            sheets.append((name, target))

    if isinstance(selector, int):
        if selector < 1 or selector > len(sheets):
            raise WorkbookError(
                f"sheet index {selector} is out of range; workbook contains {len(sheets)} sheet(s)"
            )
        return sheets[selector - 1]
    for name, target in sheets:
        if name == selector:
            return name, target
    available = ", ".join(repr(name) for name, _ in sheets)
    raise WorkbookError(f"sheet {selector!r} not found; available sheets: {available}")


def _xlsx_rows(
    archive: zipfile.ZipFile,
    sheet_path: str,
    wanted_columns: set[int],
    shared_strings: list[str],
) -> Iterator[tuple[int, dict[int, str]]]:
    try:
        stream = archive.open(sheet_path)
    except KeyError as exc:
        raise WorkbookError(f"invalid XLSX: worksheet part not found: {sheet_path}") from exc

    with stream:
        try:
            context = ET.iterparse(stream, events=("end",))
            for _, element in context:
                if _local_name(element.tag) != "row":
                    continue
                row_number = int(element.attrib.get("r", "0"))
                cells: dict[int, str] = {}
                for cell in element:
                    if _local_name(cell.tag) != "c":
                        continue
                    reference = cell.attrib.get("r", "")
                    column = _cell_column(reference)
                    if column not in wanted_columns:
                        continue
                    if _first_child(cell, "f") is not None:
                        raise WorkbookError(
                            f"formula cells are not supported in mapped fields: {reference}"
                        )
                    cell_type = cell.attrib.get("t", "")
                    value_element = _first_child(cell, "v")
                    raw = value_element.text if value_element is not None else None
                    if cell_type == "s" and raw is not None:
                        try:
                            value = shared_strings[int(raw)]
                        except (ValueError, IndexError) as exc:
                            raise WorkbookError(
                                f"invalid shared-string index {raw!r} in cell {reference}"
                            ) from exc
                    elif cell_type == "inlineStr":
                        inline = _first_child(cell, "is")
                        value = _rich_text(inline) if inline is not None else ""
                    elif cell_type == "b":
                        value = "TRUE" if raw == "1" else "FALSE"
                    elif cell_type == "e":
                        raise WorkbookError(
                            f"Excel error value {raw!r} is not supported in mapped cell {reference}"
                        )
                    else:
                        value = raw or ""
                    cells[column] = value
                yield row_number, cells
                element.clear()
        except ET.ParseError as exc:
            raise WorkbookError(f"invalid XLSX worksheet XML: {exc}") from exc


def _read_xlsx(
    path: Path, config: ExcelConfig, position_mappings: Mapping[str, str]
) -> list[SpecRow]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        if isinstance(exc, FileNotFoundError):
            raise WorkbookError(f"workbook not found: {path}") from exc
        if isinstance(exc, zipfile.BadZipFile):
            raise WorkbookError(f"invalid XLSX file: {path}") from exc
        raise WorkbookError(f"cannot read workbook {path}: {exc}") from exc
    with archive:
        sheet_name, sheet_path = _sheet_target(archive, config.sheet)
        strings = _shared_strings(archive)
        rows = _xlsx_rows(archive, sheet_path, set(config.columns.values()), strings)
        return _rows_to_specs(path, sheet_name, rows, config, position_mappings)


def _read_csv(
    path: Path, config: ExcelConfig, position_mappings: Mapping[str, str]
) -> list[SpecRow]:
    last_error: UnicodeDecodeError | None = None
    text = ""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except FileNotFoundError as exc:
            raise WorkbookError(f"workbook not found: {path}") from exc
        except OSError as exc:
            raise WorkbookError(f"cannot read workbook {path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            last_error = exc
    else:
        raise WorkbookError(f"cannot decode CSV as UTF-8 or GB18030: {path}") from last_error

    if path.suffix.lower() == ".tsv":
        dialect = csv.excel_tab
    else:
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
    parsed_rows = []
    for row_number, values in enumerate(csv.reader(io.StringIO(text), dialect), start=1):
        parsed_rows.append((row_number, {index: value for index, value in enumerate(values, start=1)}))
    return _rows_to_specs(path, "CSV", parsed_rows, config, position_mappings)


def read_spec_rows(
    path: str | Path,
    config: ExcelConfig,
    position_mappings: Mapping[str, str] | None = None,
) -> list[SpecRow]:
    workbook_path = Path(path).resolve()
    suffix = workbook_path.suffix.lower()
    mappings = position_mappings or {}
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(workbook_path, config, mappings)
    if suffix in {".csv", ".tsv"}:
        return _read_csv(workbook_path, config, mappings)
    if suffix == ".xls":
        raise WorkbookError("legacy .xls is not supported; save the workbook as .xlsx or CSV")
    raise WorkbookError(f"unsupported workbook format {suffix!r}; use .xlsx, .xlsm, .csv, or .tsv")
