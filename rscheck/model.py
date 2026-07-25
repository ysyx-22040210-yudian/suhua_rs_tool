from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


FIELD_NAMES = (
    "Intf_type",
    "RS_module",
    "RS_inst",
    "position",
    "step",
    "clk",
    "rst",
    "CRG_source",
    "RS_CFG_EN",
)

REQUIRED_ROW_FIELDS = tuple(name for name in FIELD_NAMES if name != "RS_CFG_EN")


class RsCheckError(Exception):
    """Base class for user-facing input and execution errors."""


class ConfigError(RsCheckError):
    pass


class WorkbookError(RsCheckError):
    pass


class InventoryError(RsCheckError):
    pass


class OutputError(RsCheckError):
    pass


@dataclass(frozen=True)
class ExcelConfig:
    sheet: str | int = 1
    header_row: int = 1
    data_start_row: int = 2
    validate_headers: bool = False
    columns: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RtlConfig:
    clk_port: str = "clk"
    rst_port: str = "rst"
    suffix_regex: str = r"(?P<tag>.+?)(?P<index>[0-9]+)"
    index_base: int = 0
    require_contiguous_indices: bool = False
    allow_leaf_signal_match: bool = False
    crg_match: str = "module"


@dataclass(frozen=True)
class ModuleRule:
    name: str
    has_rs_cfg_en: bool
    step_parameters: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "has_rs_cfg_en": self.has_rs_cfg_en,
            "step_parameters": list(self.step_parameters),
        }


@dataclass(frozen=True)
class ToolConfig:
    excel: ExcelConfig
    rtl: RtlConfig
    module_rules: Mapping[str, ModuleRule] = field(default_factory=dict)
    position_mappings: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SpecRow:
    source: Path
    sheet: str
    row_number: int
    intf_type: str
    rs_module: str
    rs_inst: str
    position: str
    step: int
    clk: str
    rst: str
    crg_source: str
    rs_cfg_en: str
    position_alias: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.position, self.rs_inst

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "sheet": self.sheet,
            "row": self.row_number,
            "Intf_type": self.intf_type,
            "RS_module": self.rs_module,
            "RS_inst": self.rs_inst,
            "position": self.position,
            "position_alias": self.position_alias,
            "step": self.step,
            "clk": self.clk,
            "rst": self.rst,
            "CRG_source": self.crg_source,
            "RS_CFG_EN": self.rs_cfg_en,
        }


@dataclass(frozen=True)
class DriverSource:
    instance: str
    module: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DriverSource":
        return cls(
            instance=str(value.get("instance", "")),
            module=str(value.get("module", "")),
        )


@dataclass(frozen=True)
class PortConnection:
    connection: str
    object_type: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "PortConnection":
        if isinstance(value, str):
            return cls(connection=value)
        if isinstance(value, Mapping):
            return cls(
                connection=str(value.get("connection", "")),
                object_type=str(value.get("type", "")),
            )
        return cls(connection="")


@dataclass(frozen=True)
class ActualInstance:
    name: str
    full_name: str
    module: str
    file: str
    line: int | None
    parameters: Mapping[str, str | None]
    ports: Mapping[str, PortConnection]
    clk_sources: tuple[DriverSource, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActualInstance":
        if "parameters" not in value:
            raise InventoryError("instance 'parameters' is required by schema_version 2")
        raw_parameters = value.get("parameters")
        if not isinstance(raw_parameters, Mapping):
            raise InventoryError("instance 'parameters' must be an object")
        parameters: dict[str, str | None] = {}
        for raw_name, raw_value in raw_parameters.items():
            name = str(raw_name)
            if not name:
                raise InventoryError("instance parameter names must not be empty")
            if raw_value is not None and not isinstance(raw_value, str):
                raise InventoryError(
                    f"instance parameter {name!r} value must be a string or null"
                )
            parameters[name] = raw_value
        raw_ports = value.get("ports", {})
        if not isinstance(raw_ports, Mapping):
            raise InventoryError("instance 'ports' must be an object")
        raw_sources = value.get("clk_sources", [])
        if not isinstance(raw_sources, list):
            raise InventoryError("instance 'clk_sources' must be an array")
        if not all(isinstance(item, Mapping) for item in raw_sources):
            raise InventoryError("every item in instance 'clk_sources' must be an object")
        raw_line = value.get("line")
        try:
            line = int(raw_line) if raw_line not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise InventoryError("instance 'line' must be an integer or null") from exc
        return cls(
            name=str(value.get("name", "")),
            full_name=str(value.get("full_name", "")),
            module=str(value.get("module", "")),
            file=str(value.get("file", "")),
            line=line,
            parameters=parameters,
            ports={str(k): PortConnection.from_value(v) for k, v in raw_ports.items()},
            clk_sources=tuple(DriverSource.from_mapping(v) for v in raw_sources),
        )


@dataclass(frozen=True)
class PositionInventory:
    found: bool
    instances: tuple[ActualInstance, ...]


@dataclass(frozen=True)
class Inventory:
    positions: Mapping[str, PositionInventory]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParameterEvaluation:
    name: str
    present: bool
    raw_value: str | None
    state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "raw_value": self.raw_value,
            "state": self.state,
        }


@dataclass(frozen=True)
class InstanceStepEvaluation:
    instance: str
    contribution: int | None
    parameters: tuple[ParameterEvaluation, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "contribution": self.contribution,
            "parameters": {
                parameter.name: parameter.as_dict() for parameter in self.parameters
            },
        }


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    row_number: int | None = None
    position: str = ""
    rs_inst: str = ""
    rs_cfg_en: str = ""
    instance: str = ""
    expected: Any = None
    actual: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "row": self.row_number,
            "position": self.position,
            "RS_inst": self.rs_inst,
            "RS_CFG_EN": self.rs_cfg_en,
            "instance": self.instance,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class RowResult:
    spec: SpecRow
    instances: tuple[ActualInstance, ...]
    findings: tuple[Finding, ...]
    module_rule: ModuleRule | None = None
    step_evaluations: tuple[InstanceStepEvaluation, ...] = ()

    @property
    def passed(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    @property
    def effective_step(self) -> int | None:
        if not self.instances or len(self.step_evaluations) != len(self.instances):
            return None
        contributions = [item.contribution for item in self.step_evaluations]
        if any(value is None for value in contributions):
            return None
        return sum(value for value in contributions if value is not None)


@dataclass(frozen=True)
class CheckReport:
    rows: tuple[RowResult, ...]
    global_findings: tuple[Finding, ...] = ()

    @property
    def passed(self) -> bool:
        return not any(
            item.severity == "error"
            for item in self.global_findings
        ) and all(row.passed for row in self.rows)

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.global_findings) + sum(
            item.severity == "error" for row in self.rows for item in row.findings
        )

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.global_findings) + sum(
            item.severity == "warning" for row in self.rows for item in row.findings
        )
