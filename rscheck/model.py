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

DEFAULT_CRG_TRACE_MAX_DEPTH = 16
MAX_CRG_TRACE_DEPTH = 256
CRG_TRACE_EXCLUDED_INPUTS = ("clk", "rst_n")
CRG_TRACE_STATUSES = frozenset({"complete", "depth_limited", "unresolved"})


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
    crg_trace_max_depth: int = DEFAULT_CRG_TRACE_MAX_DEPTH


@dataclass(frozen=True)
class ModuleRule:
    name: str
    has_rs_cfg_en: bool
    step_parameters: tuple[str, ...] = ()
    clk_port: str = "clk"
    rst_port: str = "rst_n"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "has_rs_cfg_en": self.has_rs_cfg_en,
            "step_parameters": list(self.step_parameters),
            "clk_port": self.clk_port,
            "rst_port": self.rst_port,
        }


def resolve_module_rule(
    module_name: str, module_rules: Mapping[str, ModuleRule]
) -> ModuleRule:
    rule = module_rules.get(module_name)
    if rule is not None:
        return rule
    return ModuleRule(
        name=module_name,
        has_rs_cfg_en=True,
        step_parameters=(),
        clk_port="clk",
        rst_port="rst_n",
    )


@dataclass(frozen=True)
class ToolConfig:
    excel: ExcelConfig
    rtl: RtlConfig
    module_rules: Mapping[str, ModuleRule] = field(default_factory=dict)
    position_mappings: Mapping[str, str] = field(default_factory=dict)
    crg_source_mappings: Mapping[str, str] = field(default_factory=dict)


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
    crg_source_alias: str = ""

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
            "crg_source_alias": self.crg_source_alias,
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
class ClockTraceNode:
    instance: str
    module: str
    depth: int
    path: tuple[str, ...]

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, max_depth: int
    ) -> "ClockTraceNode":
        instance = value.get("instance")
        module = value.get("module")
        depth = value.get("depth")
        raw_path = value.get("path")
        if not isinstance(instance, str) or not instance:
            raise InventoryError("clock trace module 'instance' must be a non-empty string")
        if not isinstance(module, str) or not module:
            raise InventoryError("clock trace module 'module' must be a non-empty string")
        if type(depth) is not int or not 1 <= depth <= max_depth:
            raise InventoryError(
                "clock trace module 'depth' must be an integer between 1 and max_depth"
            )
        if not isinstance(raw_path, list) or not all(
            isinstance(item, str) and item for item in raw_path
        ):
            raise InventoryError(
                "clock trace module 'path' must be an array of non-empty strings"
            )
        if len(raw_path) != depth or raw_path[-1] != instance:
            raise InventoryError(
                "clock trace module 'path' length/final instance is inconsistent with depth"
            )
        return cls(
            instance=instance,
            module=module,
            depth=depth,
            path=tuple(raw_path),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "module": self.module,
            "depth": self.depth,
            "path": list(self.path),
        }


@dataclass(frozen=True)
class ClockTrace:
    clock_port: str
    max_depth: int
    excluded_inputs: tuple[str, ...]
    status: str
    modules: tuple[ClockTraceNode, ...]
    diagnostics: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ClockTrace":
        clock_port = value.get("clock_port")
        max_depth = value.get("max_depth")
        excluded_inputs = value.get("excluded_inputs")
        status = value.get("status")
        raw_modules = value.get("modules")
        raw_diagnostics = value.get("diagnostics")
        if not isinstance(clock_port, str) or not clock_port.strip():
            raise InventoryError("clock_trace.clock_port must be a non-empty string")
        if type(max_depth) is not int or not 1 <= max_depth <= MAX_CRG_TRACE_DEPTH:
            raise InventoryError(
                f"clock_trace.max_depth must be an integer between 1 and {MAX_CRG_TRACE_DEPTH}"
            )
        if excluded_inputs != list(CRG_TRACE_EXCLUDED_INPUTS):
            raise InventoryError(
                "clock_trace.excluded_inputs must be exactly ['clk', 'rst_n']"
            )
        if status not in CRG_TRACE_STATUSES:
            raise InventoryError(
                "clock_trace.status must be one of: complete, depth_limited, unresolved"
            )
        if not isinstance(raw_modules, list) or not all(
            isinstance(item, Mapping) for item in raw_modules
        ):
            raise InventoryError("clock_trace.modules must be an array of objects")
        if not isinstance(raw_diagnostics, list) or not all(
            isinstance(item, str) for item in raw_diagnostics
        ):
            raise InventoryError("clock_trace.diagnostics must be an array of strings")
        modules = tuple(
            ClockTraceNode.from_mapping(item, max_depth=max_depth)
            for item in raw_modules
        )
        return cls(
            clock_port=clock_port,
            max_depth=max_depth,
            excluded_inputs=tuple(excluded_inputs),
            status=status,
            modules=modules,
            diagnostics=tuple(raw_diagnostics),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "clock_port": self.clock_port,
            "max_depth": self.max_depth,
            "excluded_inputs": list(self.excluded_inputs),
            "status": self.status,
            "modules": [module.as_dict() for module in self.modules],
            "diagnostics": list(self.diagnostics),
        }


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
    clock_trace: ClockTrace | None = None

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, schema_version: int = 2
    ) -> "ActualInstance":
        if "parameters" not in value:
            raise InventoryError(
                f"instance 'parameters' is required by schema_version {schema_version}"
            )
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
        if schema_version == 2 and "clock_trace" in value:
            raise InventoryError(
                "instance 'clock_trace' is not allowed by schema_version 2"
            )
        if schema_version == 3 and "clock_trace" not in value:
            raise InventoryError(
                "instance 'clock_trace' is required by schema_version 3"
            )
        raw_clock_trace = value.get("clock_trace")
        if raw_clock_trace is None:
            clock_trace = None
        elif isinstance(raw_clock_trace, Mapping):
            clock_trace = ClockTrace.from_mapping(raw_clock_trace)
        else:
            raise InventoryError("instance 'clock_trace' must be an object or null")
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
            clock_trace=clock_trace,
        )


@dataclass(frozen=True)
class PositionInventory:
    found: bool
    instances: tuple[ActualInstance, ...]


@dataclass(frozen=True)
class Inventory:
    positions: Mapping[str, PositionInventory]
    warnings: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    schema_version: int = 3


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
class CrgTraceEvaluation:
    instance: str
    clock_port: str
    expected: str
    max_depth: int
    status: str
    trace_status: str
    matched: ClockTraceNode | None = None
    diagnostics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "clock_port": self.clock_port,
            "expected": self.expected,
            "max_depth": self.max_depth,
            "status": self.status,
            "trace_status": self.trace_status,
            "matched": self.matched.as_dict() if self.matched is not None else None,
            "diagnostics": list(self.diagnostics),
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
    crg_evaluations: tuple[CrgTraceEvaluation, ...] = ()

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
