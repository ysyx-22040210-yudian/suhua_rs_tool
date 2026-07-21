from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .model import ConfigError, ExcelConfig, FIELD_NAMES, RtlConfig, ToolConfig


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"'{name}' must be a JSON object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise ConfigError(f"unknown keys in '{name}': {', '.join(unknown)}")


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int) or (isinstance(value, str) and value.isdigit())
    ):
        raise ConfigError(f"'{name}' must be a positive integer")
    result = int(value)
    if result < 1:
        raise ConfigError(f"'{name}' must be >= 1")
    return result


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"'{name}' must be true or false")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{name}' must be a non-empty string")
    return value.strip()


def load_config(path: str | Path) -> ToolConfig:
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read config file {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"invalid JSON in {config_path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    root = _require_mapping(raw, "root")
    excel_raw = _require_mapping(root.get("excel", {}), "excel")
    columns_raw = _require_mapping(root.get("columns", {}), "columns")
    rtl_raw = _require_mapping(root.get("rtl", {}), "rtl")
    _reject_unknown(root, {"excel", "columns", "rtl"}, "root")
    _reject_unknown(
        excel_raw,
        {"sheet", "header_row", "data_start_row", "validate_headers"},
        "excel",
    )
    _reject_unknown(
        rtl_raw,
        {
            "clk_port",
            "rst_port",
            "suffix_regex",
            "index_base",
            "require_contiguous_indices",
            "allow_leaf_signal_match",
            "crg_match",
        },
        "rtl",
    )

    missing = [name for name in FIELD_NAMES if name not in columns_raw]
    extra = [str(name) for name in columns_raw if name not in FIELD_NAMES]
    if missing:
        raise ConfigError("missing column mappings: " + ", ".join(missing))
    if extra:
        raise ConfigError("unknown column mappings: " + ", ".join(extra))

    columns = {name: _positive_int(columns_raw[name], f"columns.{name}") for name in FIELD_NAMES}
    reverse: dict[int, list[str]] = {}
    for name, index in columns.items():
        reverse.setdefault(index, []).append(name)
    duplicates = {index: names for index, names in reverse.items() if len(names) > 1}
    if duplicates:
        detail = "; ".join(f"column {index}: {', '.join(names)}" for index, names in duplicates.items())
        raise ConfigError("column mappings must be unique; " + detail)

    sheet: str | int = excel_raw.get("sheet", 1)
    if isinstance(sheet, bool) or not isinstance(sheet, (str, int)):
        raise ConfigError("'excel.sheet' must be a sheet name or 1-based index")
    if isinstance(sheet, int) and sheet < 1:
        raise ConfigError("'excel.sheet' index must be >= 1")
    if isinstance(sheet, str) and not sheet.strip():
        raise ConfigError("'excel.sheet' name cannot be empty")

    excel = ExcelConfig(
        sheet=sheet,
        header_row=_positive_int(excel_raw.get("header_row", 1), "excel.header_row"),
        data_start_row=_positive_int(excel_raw.get("data_start_row", 2), "excel.data_start_row"),
        validate_headers=_boolean(
            excel_raw.get("validate_headers", True), "excel.validate_headers"
        ),
        columns=columns,
    )
    if excel.data_start_row <= excel.header_row:
        raise ConfigError("'excel.data_start_row' must be after 'excel.header_row'")

    suffix_regex = _string(
        rtl_raw.get("suffix_regex", RtlConfig.suffix_regex), "rtl.suffix_regex"
    )
    try:
        compiled = re.compile(rf"^(?:{suffix_regex})$")
    except re.error as exc:
        raise ConfigError(f"invalid rtl.suffix_regex: {exc}") from exc
    if "index" not in compiled.groupindex:
        raise ConfigError("'rtl.suffix_regex' must contain a named group '(?P<index>...)'")

    crg_match = _string(rtl_raw.get("crg_match", "module"), "rtl.crg_match")
    if crg_match not in {"module", "instance", "module_or_instance"}:
        raise ConfigError(
            "'rtl.crg_match' must be one of: module, instance, module_or_instance"
        )

    raw_index_base = rtl_raw.get("index_base", 0)
    if isinstance(raw_index_base, bool) or not isinstance(raw_index_base, int):
        raise ConfigError("'rtl.index_base' must be an integer")
    if raw_index_base < 0:
        raise ConfigError("'rtl.index_base' must be >= 0")
    rtl = RtlConfig(
        clk_port=_string(rtl_raw.get("clk_port", "clk"), "rtl.clk_port"),
        rst_port=_string(rtl_raw.get("rst_port", "rst"), "rtl.rst_port"),
        suffix_regex=suffix_regex,
        index_base=raw_index_base,
        require_contiguous_indices=_boolean(
            rtl_raw.get("require_contiguous_indices", False),
            "rtl.require_contiguous_indices",
        ),
        allow_leaf_signal_match=_boolean(
            rtl_raw.get("allow_leaf_signal_match", False),
            "rtl.allow_leaf_signal_match",
        ),
        crg_match=crg_match,
    )

    return ToolConfig(excel=excel, rtl=rtl)
