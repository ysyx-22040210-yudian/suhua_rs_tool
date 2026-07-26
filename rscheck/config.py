from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .model import (
    ConfigError,
    ExcelConfig,
    FIELD_NAMES,
    ModuleRule,
    RtlConfig,
    ToolConfig,
)


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"'{name}' must be a JSON object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise ConfigError(f"unknown keys in '{name}': {', '.join(unknown)}")


def _positive_int(value: Any, name: str) -> int:
    string_integer = (
        isinstance(value, str) and value.isascii() and value.isdecimal()
    )
    if isinstance(value, bool) or not (isinstance(value, int) or string_integer):
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


def _module_rules(
    value: Any,
    *,
    legacy_clk_port: str,
    legacy_rst_port: str,
) -> dict[str, ModuleRule]:
    raw_rules = _require_mapping(value, "module_rules")
    rules: dict[str, ModuleRule] = {}
    for raw_name, raw_rule in raw_rules.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ConfigError("'module_rules' names must be non-empty strings")
        name = raw_name.strip()
        if name != raw_name:
            raise ConfigError(f"module rule name must not have surrounding whitespace: {raw_name!r}")
        rule = _require_mapping(raw_rule, f"module_rules.{name}")
        _reject_unknown(
            rule,
            {"has_rs_cfg_en", "step_parameters", "clk_port", "rst_port"},
            f"module_rules.{name}",
        )
        if "has_rs_cfg_en" not in rule:
            raise ConfigError(f"'module_rules.{name}.has_rs_cfg_en' is required")
        if "step_parameters" not in rule:
            raise ConfigError(f"'module_rules.{name}.step_parameters' is required")
        has_rs_cfg_en = _boolean(
            rule["has_rs_cfg_en"], f"module_rules.{name}.has_rs_cfg_en"
        )
        raw_parameters = rule["step_parameters"]
        if not isinstance(raw_parameters, list):
            raise ConfigError(
                f"'module_rules.{name}.step_parameters' must be a JSON array"
            )
        parameters: list[str] = []
        for index, raw_parameter in enumerate(raw_parameters):
            parameter = _string(
                raw_parameter,
                f"module_rules.{name}.step_parameters[{index}]",
            )
            if parameter != raw_parameter or any(
                character.isspace() for character in parameter
            ):
                raise ConfigError(
                    f"'module_rules.{name}.step_parameters[{index}]' "
                    "must not contain whitespace"
                )
            if parameter == "RS_CRG_EN":
                raise ConfigError(
                    f"'module_rules.{name}.step_parameters' must not include RS_CRG_EN"
                )
            if parameter in parameters:
                raise ConfigError(
                    f"duplicate step parameter {parameter!r} in module rule {name!r}"
                )
            parameters.append(parameter)
        rules[name] = ModuleRule(
            name=name,
            has_rs_cfg_en=has_rs_cfg_en,
            step_parameters=tuple(parameters),
            clk_port=_string(
                rule.get("clk_port", legacy_clk_port),
                f"module_rules.{name}.clk_port",
            ),
            rst_port=_string(
                rule.get("rst_port", legacy_rst_port),
                f"module_rules.{name}.rst_port",
            ),
        )
    return rules


def _position_mappings(value: Any) -> dict[str, str]:
    raw_mappings = _require_mapping(value, "position_mappings")
    mappings: dict[str, str] = {}
    for raw_alias, raw_position in raw_mappings.items():
        if not isinstance(raw_alias, str) or not raw_alias:
            raise ConfigError("'position_mappings' keys must be non-empty strings")
        if raw_alias != raw_alias.strip():
            raise ConfigError(
                f"position mapping key must not have surrounding whitespace: {raw_alias!r}"
            )
        if raw_alias.startswith(".") or raw_alias.endswith("."):
            raise ConfigError(
                f"position mapping key must not start or end with '.': {raw_alias!r}"
            )
        if not isinstance(raw_position, str) or not raw_position:
            raise ConfigError(
                f"'position_mappings.{raw_alias}' must be a non-empty string"
            )
        if raw_position != raw_position.strip():
            raise ConfigError(
                "position mapping value must not have surrounding whitespace: "
                f"{raw_position!r}"
            )
        if not raw_position.strip("."):
            raise ConfigError(
                f"'position_mappings.{raw_alias}' must contain a non-empty RTL path"
            )
        mappings[raw_alias] = raw_position
    return mappings


def make_position_mappings(mappings: Mapping[str, str]) -> dict[str, str]:
    """Validate and normalize an in-memory position mapping database."""
    return _position_mappings(dict(mappings))


COMPLETE_CONFIG_ROOTS = frozenset(
    {"excel", "columns", "rtl", "position_mappings", "module_rules"}
)


def _read_config_document(path: str | Path) -> Any:
    config_path = Path(path)
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read config file {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"invalid JSON in {config_path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def load_config(path: str | Path) -> ToolConfig:
    return config_from_dict(_read_config_document(path))


def load_complete_config(path: str | Path) -> ToolConfig:
    raw = _read_config_document(path)
    root = _require_mapping(raw, "root")
    missing = sorted(COMPLETE_CONFIG_ROOTS - set(root))
    if missing:
        raise ConfigError(
            "complete config is missing root sections: " + ", ".join(missing)
        )
    return config_from_dict(raw)


def config_from_dict(raw: Any) -> ToolConfig:
    """Validate and normalize an in-memory configuration document."""
    root = _require_mapping(raw, "root")
    excel_raw = _require_mapping(root.get("excel", {}), "excel")
    columns_raw = _require_mapping(root.get("columns", {}), "columns")
    rtl_raw = _require_mapping(root.get("rtl", {}), "rtl")
    position_mappings = _position_mappings(root.get("position_mappings", {}))
    _reject_unknown(
        root,
        set(COMPLETE_CONFIG_ROOTS),
        "root",
    )
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
            excel_raw.get("validate_headers", False), "excel.validate_headers"
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
    module_rules = _module_rules(
        root.get("module_rules"),
        legacy_clk_port=rtl.clk_port,
        legacy_rst_port=rtl.rst_port,
    )

    return ToolConfig(
        excel=excel,
        rtl=rtl,
        module_rules=module_rules,
        position_mappings=position_mappings,
    )


def config_to_dict(config: ToolConfig) -> dict[str, Any]:
    return {
        "excel": {
            "sheet": config.excel.sheet,
            "header_row": config.excel.header_row,
            "data_start_row": config.excel.data_start_row,
            "validate_headers": config.excel.validate_headers,
        },
        "columns": dict(config.excel.columns),
        "rtl": {
            "clk_port": config.rtl.clk_port,
            "rst_port": config.rtl.rst_port,
            "suffix_regex": config.rtl.suffix_regex,
            "index_base": config.rtl.index_base,
            "require_contiguous_indices": config.rtl.require_contiguous_indices,
            "allow_leaf_signal_match": config.rtl.allow_leaf_signal_match,
            "crg_match": config.rtl.crg_match,
        },
        "position_mappings": {
            alias: position
            for alias, position in sorted(config.position_mappings.items())
        },
        "module_rules": {
            name: {
                "has_rs_cfg_en": rule.has_rs_cfg_en,
                "step_parameters": list(rule.step_parameters),
                "clk_port": rule.clk_port,
                "rst_port": rule.rst_port,
            }
            for name, rule in sorted(config.module_rules.items())
        },
    }


def save_config(config: ToolConfig, path: str | Path) -> Path:
    normalized = config_from_dict(config_to_dict(config))
    payload = (
        json.dumps(config_to_dict(normalized), ensure_ascii=False, indent=2) + "\n"
    )
    requested_output = Path(path)
    output = requested_output
    temporary_path: Path | None = None
    try:
        output = requested_output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
        os.replace(temporary_path, output)
    except (OSError, RuntimeError) as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ConfigError(f"cannot save config file {requested_output}: {exc}") from exc
    return output
