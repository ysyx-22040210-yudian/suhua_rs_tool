from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from bisect import bisect_right
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping
from xml.etree import ElementTree as ET

from .model import (
    REQUIRED_ROW_FIELDS,
    ROUTE_FIELDS,
    ExcelConfig,
    SkippedRow,
    SpecRow,
    StepSumCheck,
    WorkbookError,
    skip_reason,
    with_route_groups,
)


_CELL_REF_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
_MERGE_CELL = re.compile(r"\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})")
_EXCEL_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?")
_XLSX_TEXT_ESCAPE = re.compile(r"_x000[9ADad]_|_x005[Ff]_(?=x[0-9A-Fa-f]{4}_)")
_XLSX_TEXT_CHARS = {"_x0009_": "\t", "_x000d_": "\r", "_x000a_": "\n", "_x005f_": "_"}
_RECALCULATE = "Recalculate the workbook in Excel or LibreOffice, save it, then retry."


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


def _parse_count(value: str, label: str) -> int:
    if not re.fullmatch(r"[0-9]+(?:\.0+)?", value):
        raise WorkbookError(f"{label} must be a non-negative integer, got {value!r}")
    return int(Decimal(value))


class _StepSumColumn:
    """Join merge metadata (usually after sheetData) with active rows in the same sheet."""

    def __init__(self, sheet: str, config: ExcelConfig):
        self.sheet = sheet
        self.column = config.columns["step_sum"]
        n, self.letter = self.column, ""
        while n > 0:
            n, digit = divmod(n - 1, 26)
            self.letter = chr(65 + digit) + self.letter
        self.data_start = config.data_start_row
        self.spans: list[tuple[int, int]] = []
        # A subtotal on an excluded row can still own other, active rows.
        self.cells: dict[int, tuple[str, str, bool]] = {}

    def add_merge(self, ref: str) -> None:
        ends = ref.upper().split(":")
        left, right = _MERGE_CELL.fullmatch(ends[0]), _MERGE_CELL.fullmatch(ends[-1])
        if len(ends) > 2 or not left or not right:
            raise WorkbookError(f"{self.sheet}!{ref}: invalid Excel merge reference")
        c1, c2 = column_letters_to_index(left[1]), column_letters_to_index(right[1])
        start, end = int(left[2]), int(right[2])
        if not min(c1, c2) <= self.column <= max(c1, c2) or max(start, end) < self.data_start:
            return
        if c1 != c2 or not self.data_start <= start <= end <= 1048576:
            raise WorkbookError(f"{self.sheet}!{ref} (step_sum): 仅支持数据区内同一列的纵向合并")
        self.spans.append((start, end))

    def apply(self, specs: list[SpecRow], on_notice: Callable[[str], None] | None) -> list[SpecRow]:
        spans = sorted(self.spans)
        for (_, prev_end), (start, end) in zip(spans, spans[1:]):
            if start <= prev_end:
                raise WorkbookError(f"{self.sheet}!{self.letter}{start}:{self.letter}{end} (step_sum): 合并区域重叠")
        starts = [start for start, _ in spans]
        groups: dict[tuple[int, int], list[int]] = {}
        for index, spec in enumerate(specs):
            row = spec.row_number
            pos = bisect_right(starts, row) - 1
            span = spans[pos] if pos >= 0 and row <= spans[pos][1] else (row, row)
            groups.setdefault(span, []).append(index)
        formula_cells = []
        for (start, end), indices in groups.items():
            anchor = f"{self.letter}{start}"
            location = f"{self.sheet}!{anchor}" + (f":{self.letter}{end}" if end != start else "")
            value, error, formula = self.cells.get(start, ("", "", False))
            if error:
                raise WorkbookError(error)
            if formula:
                formula_cells.append(anchor)
            expected = value if skip_reason(value) else _parse_count(value, f"{location}: step_sum")
            members = tuple((specs[i].row_number, specs[i].step) for i in indices)
            owner = indices[0]
            specs[owner] = replace(specs[owner], step_sum_check=StepSumCheck(location, expected, members))
        if formula_cells and on_notice:
            on_notice(f"info[EXCEL_FORMULA_CACHE]: {self.sheet}: used saved results for {len(formula_cells)} "
                      f"step_sum formula cell(s) (e.g. {', '.join(formula_cells[:3])}); if inputs changed, recalculate and save.")
        return specs


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


def _xlsx_text(value: str) -> str:
    """Restore XLSX tabs/line breaks without reinterpreting protected literal markers."""
    if "_x" not in value:
        return value
    return _XLSX_TEXT_ESCAPE.sub(lambda m: _XLSX_TEXT_CHARS[m[0].lower()], value)


def _rich_text(container: ET.Element) -> str:
    parts: list[str] = []
    for child in container:
        if _local_name(child.tag) == "t":
            parts.append(_xlsx_text(child.text or ""))
        elif _local_name(child.tag) == "r":
            text = _first_child(child, "t")
            if text is not None:
                parts.append(_xlsx_text(text.text or ""))
    return "".join(parts)


@dataclass(frozen=True)
class _InputRow:
    number: int
    cells: Mapping[int, str] | SkippedRow
    rs_inst_formula_cell: str | None = None
    route_errors: Mapping[str, str] | None = None


def _rows_to_specs(
    path: Path,
    sheet_name: str,
    rows: Iterable[_InputRow],
    config: ExcelConfig,
    position_mappings: Mapping[str, str],
    crg_source_mappings: Mapping[str, str],
    on_skip: Callable[[SkippedRow], None] | None,
) -> list[SpecRow]:
    specs: list[SpecRow] = []
    errors: list[str] = []
    saw_data_row = False
    header_seen = False
    seen: dict[tuple[str, ...], tuple[int, str]] = {}
    for row in rows:
        row_number, cells = row.number, row.cells
        if isinstance(cells, SkippedRow):
            saw_data_row = True
            if on_skip:
                on_skip(cells)
            continue
        if config.validate_headers and row_number == config.header_row:
            _validate_headers([(row_number, cells)], config)
            header_seen = True
        if row_number < config.data_start_row:
            continue
        saw_data_row = True
        values = {
            field_name: _normalise_text(cells.get(column, ""))
            for field_name, column in config.columns.items()
        }
        if not any(values.values()) and not any(_normalise_text(v) for v in cells.values()):
            continue
        excluded = {f: reason for f in REQUIRED_ROW_FIELDS if (reason := skip_reason(values[f]))}
        if excluded:
            if on_skip:
                on_skip(SkippedRow(sheet_name, row_number, excluded))
            continue
        # Keep physical newlines, including a final one after a continuation.
        # Tokenization and JSON/GUI validation must see the same original text.
        raw_inst = cells.get(config.columns["RS_inst"], "")
        if "\n" in raw_inst or "\r" in raw_inst:
            values["RS_inst"] = raw_inst
        try:
            step = _parse_count(values["step"], f"row {row_number}: step")
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
        crg_source_input = values["CRG_source"].strip(".")
        if skip_reason(values["CRG_source"]) is None and crg_source_input in crg_source_mappings:
            crg_source = crg_source_mappings[crg_source_input].strip(".")
            crg_source_alias = crg_source_input
        else:
            crg_source = crg_source_input
            crg_source_alias = ""

        spec = SpecRow(
            source=path,
            sheet=sheet_name,
            row_number=row_number,
            intf_type=values["Intf_type"],
            rs_module=values["RS_module"],
            rs_inst=values["RS_inst"],
            rs_inst_formula_cell=row.rs_inst_formula_cell,
            rs_route=values.get("RS_route", ""),
            rs_route_mapped="RS_route" in config.columns,
            rs_route2=values.get("RS_route2", ""),
            rs_route2_mapped="RS_route2" in config.columns,
            route_cell_error=(row.route_errors or {}).get("RS_route", ""),
            route2_cell_error=(row.route_errors or {}).get("RS_route2", ""),
            position=position,
            step=step,
            clk=values["clk"],
            rst=values["rst"],
            crg_source=crg_source,
            rs_cfg_en=values["RS_CFG_EN"],
            position_alias=position_alias,
            crg_source_alias=crg_source_alias,
        )
        if not spec.position:
            errors.append(f"row {row_number}: position cannot be empty")
            continue
        if not spec.crg_source and values["CRG_source"]:
            errors.append(f"row {row_number}: CRG_source cannot be empty")
            continue
        # Equal instance text with distinct route hints may represent different
        # intended instances. Detect an actual shared owner after RTL matching.
        key = spec.key + (spec.rs_module,)
        if spec.rs_inst_items and not spec.has_route and not spec.route_cell_error and key in seen:
            first_row, first_position_input = seen[key]
            errors.append(
                f"row {row_number}: duplicate group ({spec.position}, {spec.selector_label}) "
                f"after position resolution from {position_input!r}; first defined "
                f"from {first_position_input!r} at row {first_row}"
            )
            continue
        if not spec.has_route and not spec.route_cell_error:
            seen[key] = (row_number, position_input)
        specs.append(spec)

    if config.validate_headers and not header_seen:
        raise WorkbookError(f"header row {config.header_row} was not found")
    if errors:
        raise WorkbookError("invalid workbook data:\n  " + "\n  ".join(errors))
    if not saw_data_row:
        raise WorkbookError("no specification rows were found")
    return with_route_groups(specs)


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


def _xlsx_cell_value(
    cell: ET.Element, shared_strings: list[str], location: str, *, formula: bool, numeric_field: str
) -> str:
    """Read the stored value; never interpret the formula or follow external references."""
    kind = cell.get("t", "n")
    value = _first_child(cell, "v")
    raw = value.text if value is not None and value.text is not None else ""
    inline = _first_child(cell, "is") if kind == "inlineStr" else None

    if formula:
        # Only an explicitly stored empty *string* is an empty result. In
        # particular, many non-calculating writers emit <f>...</f><v/> (numeric).
        missing = inline is None if kind == "inlineStr" else (
            value is None or (kind != "str" and not raw.strip())
        )
        if missing:
            raise WorkbookError(f"{location}: formula has no cached result. {_RECALCULATE}")
    if kind == "e":
        reason = "formula returned Excel error" if formula else "Excel error value"
        raise WorkbookError(f"{location}: {reason} {raw!r}. Fix the referenced cells and save.")
    if kind == "inlineStr":
        return _rich_text(inline) if inline is not None else ""
    if kind == "str":
        return _xlsx_text(raw)
    if kind == "s" and raw:
        try:
            index = int(raw)
            if index < 0:
                raise IndexError
            return shared_strings[index]
        except (ValueError, IndexError) as exc:
            raise WorkbookError(f"{location}: invalid shared-string index {raw!r}") from exc
    if kind == "b":
        if raw in ("1", "true"):
            return "TRUE"
        if raw in ("0", "false"):
            return "FALSE"
        raise WorkbookError(f"{location}: invalid Excel boolean {raw!r}")
    if formula and kind == "n":
        if not _EXCEL_NUMBER.fullmatch(raw):
            raise WorkbookError(f"{location}: invalid cached number {raw!r}. {_RECALCULATE}")
        if numeric_field:
            # Excel may serialize an integral number in scientific notation.
            # Limit expansion to Excel's numeric range before converting to int.
            try:
                number = Decimal(raw)
                if number.is_zero():
                    return "0"
                if (
                    number > 0
                    and 0 <= number.adjusted() <= 308
                    and number == number.to_integral_value()
                ):
                    return str(int(number))
            except InvalidOperation:
                pass
            raise WorkbookError(f"{location}: {numeric_field} must be a non-negative integer, got {raw!r}")
    elif formula and kind != "d":
        raise WorkbookError(f"{location}: unsupported formula result type {kind!r}. {_RECALCULATE}")
    return raw


def _xlsx_rows(
    archive: zipfile.ZipFile,
    sheet_path: str,
    sheet_name: str,
    config: ExcelConfig,
    shared_strings: list[str],
    on_notice: Callable[[str], None] | None,
    totals: _StepSumColumn | None,
) -> Iterator[_InputRow]:
    field_for_column = {column: name for name, column in config.columns.items()}
    formula_count = 0
    examples: list[str] = []
    try:
        stream = archive.open(sheet_path)
    except KeyError as exc:
        raise WorkbookError(f"invalid XLSX: worksheet part not found: {sheet_path}") from exc

    with stream:
        try:
            context = ET.iterparse(stream, events=("end",))
            for _, element in context:
                tag = _local_name(element.tag)
                if tag == "mergeCell" and totals is not None:
                    totals.add_merge(element.get("ref", ""))
                if tag != "row":
                    continue
                row_number = int(element.attrib.get("r", "0"))
                is_data = row_number >= config.data_start_row
                if not is_data and not (config.validate_headers and row_number == config.header_row):
                    element.clear()
                    continue
                mapped: dict[int, ET.Element] = {}
                for cell in element:
                    if _local_name(cell.tag) != "c":
                        continue
                    reference = cell.attrib.get("r", "")
                    column = _cell_column(reference)
                    if column in field_for_column:
                        mapped[column] = cell

                cells: dict[int, str] = {}
                formulas: list[str] = []
                rs_inst_formula_cell = None

                def read(column: int) -> None:
                    nonlocal rs_inst_formula_cell
                    cell = mapped[column]
                    reference = cell.attrib["r"]
                    field_name = field_for_column[column]
                    formula = _first_child(cell, "f") is not None
                    location = f"{sheet_name}!{reference} ({field_name})"
                    cells[column] = _xlsx_cell_value(
                        cell, shared_strings, location, formula=formula,
                        numeric_field=field_name if is_data and field_name in ("step", "step_sum") else "",
                    )
                    if formula:
                        formulas.append(reference)
                        if is_data and field_name == "RS_inst":
                            # Only the cached result is a selector. Record its address
                            # for review without exporting or interpreting the formula.
                            rs_inst_formula_cell = reference.upper()

                if is_data:
                    if totals is not None and totals.column in mapped:
                        col = totals.column
                        cell = mapped[col]
                        formula = _first_child(cell, "f") is not None
                        # A missing/invalid anchor matters only if its range contains
                        # an active row. Do not evaluate it as a field on the anchor row.
                        try:
                            value = _xlsx_cell_value(cell, shared_strings, f"{sheet_name}!{cell.get('r')} (step_sum)",
                                                     formula=formula, numeric_field="step_sum").strip()
                            if value or formula:
                                totals.cells[row_number] = (value, "", formula)
                        except WorkbookError as exc:
                            totals.cells[row_number] = ("", str(exc), formula)
                        del mapped[col]
                    gate_errors = {}
                    excluded = {}
                    for field in REQUIRED_ROW_FIELDS:
                        column = config.columns[field]
                        try:
                            if column in mapped:
                                read(column)
                        except WorkbookError as exc:
                            gate_errors[field] = exc
                            continue
                        reason = skip_reason(cells.get(column, ""))
                        if reason:
                            excluded[field] = reason
                    if excluded:
                        # Ignore unused/styled empty rows, but keep an audit record for
                        # excluded data rows. Never evaluate other formulas or their caches.
                        populated = (any(v.strip() for v in cells.values()) or gate_errors or formulas or
                                     any(_first_child(c, "f") is not None or
                                         any((e.text or "").strip() for e in c.iter() if _local_name(e.tag) in {"v", "t"})
                                         for c in element if _local_name(c.tag) == "c"))
                        yield _InputRow(row_number, SkippedRow(sheet_name, row_number, excluded) if populated else {})
                        element.clear()
                        continue
                    if gate_errors:
                        raise next(iter(gate_errors.values()))
                route_errors = {}
                for column in mapped:
                    if column not in cells:
                        try:
                            read(column)
                        except WorkbookError as exc:
                            field = field_for_column[column]
                            if not is_data or field not in ROUTE_FIELDS:
                                raise
                            route_errors[field] = str(exc)
                formula_count += len(formulas)
                examples.extend(formulas[:3 - len(examples)])
                yield _InputRow(row_number, cells, rs_inst_formula_cell, route_errors)
                element.clear()
        except ET.ParseError as exc:
            raise WorkbookError(f"invalid XLSX worksheet XML: {exc}") from exc
    if formula_count and on_notice is not None:
        on_notice(
            f"info[EXCEL_FORMULA_CACHE]: {sheet_name}: used saved results for {formula_count} "
            f"formula cell(s) (e.g. {', '.join(examples)}); if inputs changed, recalculate and save."
        )


def _read_xlsx(
    path: Path,
    config: ExcelConfig,
    position_mappings: Mapping[str, str],
    crg_source_mappings: Mapping[str, str],
    on_notice: Callable[[str], None] | None,
    on_skip: Callable[[SkippedRow], None] | None,
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
        totals = _StepSumColumn(sheet_name, config) if "step_sum" in config.columns else None
        rows = _xlsx_rows(archive, sheet_path, sheet_name, config, strings, on_notice, totals)
        specs = _rows_to_specs(
            path,
            sheet_name,
            rows,
            config,
            position_mappings,
            crg_source_mappings,
            on_skip,
        )
        return totals.apply(specs, on_notice) if totals is not None else specs


def _read_csv(
    path: Path,
    config: ExcelConfig,
    position_mappings: Mapping[str, str],
    crg_source_mappings: Mapping[str, str],
    on_skip: Callable[[SkippedRow], None] | None,
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
    totals = _StepSumColumn("CSV", config) if "step_sum" in config.columns else None
    def parsed_rows() -> Iterator[_InputRow]:
        for number, values in enumerate(csv.reader(io.StringIO(text), dialect), start=1):
            cells = dict(enumerate(values, start=1))
            if totals is not None and number >= config.data_start_row and cells.get(totals.column):
                totals.cells[number] = (cells[totals.column].strip(), "", False)
            yield _InputRow(number, cells)
    specs = _rows_to_specs(
        path,
        "CSV",
        parsed_rows(),
        config,
        position_mappings,
        crg_source_mappings,
        on_skip,
    )
    return totals.apply(specs, None) if totals is not None else specs


def read_spec_rows(
    path: str | Path,
    config: ExcelConfig,
    position_mappings: Mapping[str, str] | None = None,
    crg_source_mappings: Mapping[str, str] | None = None,
    *,
    on_notice: Callable[[str], None] | None = None,
    on_skip: Callable[[SkippedRow], None] | None = None,
) -> list[SpecRow]:
    workbook_path = Path(path).resolve()
    suffix = workbook_path.suffix.lower()
    mappings = position_mappings or {}
    crg_mappings = crg_source_mappings or {}
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(workbook_path, config, mappings, crg_mappings, on_notice, on_skip)
    if suffix in {".csv", ".tsv"}:
        return _read_csv(workbook_path, config, mappings, crg_mappings, on_skip)
    if suffix == ".xls":
        raise WorkbookError("legacy .xls is not supported; save the workbook as .xlsx or CSV")
    raise WorkbookError(f"unsupported workbook format {suffix!r}; use .xlsx, .xlsm, .csv, or .tsv")
