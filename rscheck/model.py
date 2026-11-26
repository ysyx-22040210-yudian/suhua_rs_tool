from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping


REQUIRED_COLUMN_FIELDS = (
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

ROUTE_FIELDS = ("RS_route", "RS_route2")
FIELD_NAMES = REQUIRED_COLUMN_FIELDS + ROUTE_FIELDS
SELECTOR_POLICY = "inst_then_route_by_excel_group"
REQUIRED_ROW_FIELDS = ("Intf_type", "RS_module", "position", "step")
OPTIONAL_ROW_FIELDS = tuple(name for name in FIELD_NAMES if name not in REQUIRED_ROW_FIELDS)


def skip_reason(value: str) -> str | None:
    """Only the whole, trimmed, case-sensitive marker is special."""
    value = value.strip()
    return "blank" if not value else "NA" if value == "NA" else None
NAME_CANDIDATE_LIMIT = 5
NAME_TIE_REVIEW_LIMIT = 50
RS_INST_GROUP_RULE = "exact_name_or_prefix_with_configured_suffix"
RS_INST_FUZZY_RULE = "nearest_name_after_no_group_match"
RS_INST_LIST_RULE = "per_item_union"
RS_INST_LEGACY_SPLIT_RULE = "semicolon_or_linebreak"
RS_INST_SPLIT_RULE = "whitespace_semicolon_comma_dunhao_colon"
_RS_INST_PUNCTUATION = ";；,，、:："
# Skip an RTL escaped identifier as one unit (it ends at whitespace). Group
# horizontal whitespace with an immediately following newline for continuation handling.
_RS_INST_BOUNDARIES = {
    RS_INST_SPLIT_RULE: re.compile(
        rf"(?<![^\s{_RS_INST_PUNCTUATION}])\\\S+|"
        rf"(?P<boundary>[^\S\r\n]+(?:\r\n|[\r\n])?|\r\n|[\r\n{_RS_INST_PUNCTUATION}])"
    ),
    RS_INST_LEGACY_SPLIT_RULE: re.compile(r"(?<![^\s;；])\\\S+|(?P<boundary>[;；]|\r\n|[\r\n])"),
}


def split_rs_inst(value: str, *, rule: str = RS_INST_SPLIT_RULE) -> tuple[str, ...]:
    """Return selectors in source order; the legacy rule is for saved reports only."""
    items = []
    start = escaped_end = 0
    for match in _RS_INST_BOUNDARIES[rule].finditer(value):
        if match.lastgroup is None:  # part of the segment, not a separator
            escaped_end = match.end()
            continue
        segment = value[start:match.start()].rstrip()
        if match[0][-1] in "\r\n" and segment.endswith("\\") and start + len(segment) != escaped_end:
            segment = segment[:-1]
        token = segment.strip()
        if token:
            items.append(token)
        start = match.end()
    tail = value[start:].strip()
    if tail:
        items.append(tail)
    return tuple(items)


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
    rs_route: str = ""
    rs_route_mapped: bool = False
    rs_inst_formula_cell: str | None = None
    rs_route2: str = ""
    rs_route2_mapped: bool = False
    route_group_size: int = 0
    # Route errors are conditional: an inactive route formula must not prevent
    # an exact/fuzzy RS_inst success (or an empty-selector row) from running.
    route_cell_error: str = ""
    route2_cell_error: str = ""

    @property
    def ignored_fields(self) -> dict[str, str]:
        values = {"RS_inst": self.rs_inst, "clk": self.clk, "rst": self.rst,
                  "CRG_source": self.crg_source_alias or self.crg_source, "RS_CFG_EN": self.rs_cfg_en}
        if (self.rs_route_mapped or self.rs_route) and self.rs_inst_items and not self.route_cell_error:
            values["RS_route"] = self.rs_route
        return {field: reason for field, value in values.items() if (reason := skip_reason(value))}

    @property
    def blank_findings(self) -> tuple[Finding, ...]:
        return tuple(Finding("warning", "EXCEL_FIELD_BLANK",
                             blank_field_message(self.sheet, self.row_number, name),
                             self.row_number, self.position, self.rs_inst, self.rs_cfg_en,
                             actual={"field": name, "reason": "blank"})
                     for name, reason in self.ignored_fields.items() if reason == "blank")

    @property
    def has_route(self) -> bool:
        return skip_reason(self.rs_route) is None

    @property
    def secondary_group(self) -> bool:
        return self.has_route and self.route_group_size > 1

    @property
    def route_field(self) -> str:
        return "RS_route2" if self.secondary_group else "RS_route"

    @property
    def rs_inst_items(self) -> tuple[str, ...]:
        return split_rs_inst(self.rs_inst) if skip_reason(self.rs_inst) is None else ()

    @property
    def key(self) -> tuple[str, ...]:
        items = self.rs_inst_items
        if items:
            return ("inst", self.position, *sorted(set(items)))
        return "route", self.position, self.rs_module, self.rs_route

    @property
    def selector_label(self) -> str:
        items = self.rs_inst_items
        if not items:
            return "未指定例化"
        # Keep a single line per specification in console summaries. JSON/CSV retain the cell value.
        if "\n" in self.rs_inst or "\r" in self.rs_inst:
            return "; ".join(items)
        return self.rs_inst

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "sheet": self.sheet,
            "row": self.row_number,
            "Intf_type": self.intf_type,
            "RS_module": self.rs_module,
            "RS_inst": self.rs_inst,
            **({"RS_inst_formula_cell": self.rs_inst_formula_cell} if self.rs_inst_formula_cell else {}),
            "RS_inst_items": list(self.rs_inst_items),
            "RS_inst_split_rule": RS_INST_SPLIT_RULE,
            "RS_route": self.rs_route,
            "RS_route2": self.rs_route2,
            "selector_policy": SELECTOR_POLICY,
            "route_group_size": self.route_group_size,
            "route_mapped": self.rs_route_mapped or bool(self.rs_route),
            "route2_mapped": self.rs_route2_mapped or bool(self.rs_route2),
            "position": self.position,
            "position_alias": self.position_alias,
            "step": self.step,
            "clk": self.clk,
            "rst": self.rst,
            "CRG_source": self.crg_source,
            "crg_source_alias": self.crg_source_alias,
            "RS_CFG_EN": self.rs_cfg_en,
            "ignored_fields": self.ignored_fields,
        }


def with_route_groups(specs: Iterable[SpecRow]) -> list[SpecRow]:
    """Count active rows in the same sheet; aliases are already resolved."""
    rows = list(specs)
    keys = [(s.source, s.sheet, s.rs_module, s.position, s.rs_route)
            if s.rs_inst_items and s.has_route else None for s in rows]
    counts = Counter(key for key in keys if key is not None)
    return [s if s.route_group_size == counts[key] else replace(s, route_group_size=counts[key])
            for s, key in zip(rows, keys)]


@dataclass(frozen=True)
class SkippedRow:
    sheet: str
    row: int
    fields: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {"sheet": self.sheet, "row": self.row, "fields": dict(self.fields)}

    def describe(self) -> str:
        return f"{self.sheet} 第 {self.row} 行：" + "、".join(
            f"{key}={'空' if reason == 'blank' else 'NA'}" for key, reason in self.fields.items())


def blank_field_message(sheet: str, row: int, name: str) -> str:
    return f"{sheet} 第 {row} 行：{name} 为空，已跳过该项检查。"


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
class SourceEndpoint:
    instance: str
    port: str
    connection: str
    depth: int
    path: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"instance": self.instance, "port": self.port, "connection": self.connection,
                "depth": self.depth, "path": list(self.path)}


@dataclass(frozen=True)
class SourceSignalCheck:
    instance: str
    role: str
    start_port: str
    target: str
    expected: str
    status: str
    endpoints: tuple[SourceEndpoint, ...]
    matching: tuple[SourceEndpoint, ...] = ()
    diagnostics: tuple[str, ...] = ()
    allow_leaf_signal_match: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"instance": self.instance, "role": self.role, "start_port": self.start_port,
                "target": self.target, "expected": self.expected, "status": self.status,
                "endpoints": [e.as_dict() for e in self.endpoints],
                "matching": [e.as_dict() for e in self.matching], "diagnostics": list(self.diagnostics),
                "allow_leaf_signal_match": self.allow_leaf_signal_match}


@dataclass(frozen=True)
class ClockTrace:
    clock_port: str
    max_depth: int
    excluded_inputs: tuple[str, ...]
    status: str
    modules: tuple[ClockTraceNode, ...]
    diagnostics: tuple[str, ...] = ()
    endpoints: tuple[SourceEndpoint, ...] = ()
    is_reset: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, reset: bool = False, endpoints_required: bool = False) -> "ClockTrace":
        clock_port = value.get("reset_port" if reset else "clock_port")
        max_depth = value.get("max_depth")
        excluded_inputs = value.get("excluded_inputs")
        status = value.get("status")
        raw_modules = value.get("modules")
        raw_diagnostics = value.get("diagnostics")
        label = "reset_trace" if reset else "clock_trace"
        if not isinstance(clock_port, str) or not clock_port.strip():
            raise InventoryError(f"{label} start port must be a non-empty string")
        if type(max_depth) is not int or not 1 <= max_depth <= MAX_CRG_TRACE_DEPTH:
            raise InventoryError(
                f"clock_trace.max_depth must be an integer between 1 and {MAX_CRG_TRACE_DEPTH}"
            )
        required_exclusions = ["clk"] if reset else list(CRG_TRACE_EXCLUDED_INPUTS)
        if excluded_inputs != required_exclusions:
            raise InventoryError(
                f"{label}.excluded_inputs must be exactly {required_exclusions!r}"
            )
        if not isinstance(status, str) or status not in CRG_TRACE_STATUSES:
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
        raw_endpoints = value.get("endpoints", None if endpoints_required else [])
        if not isinstance(raw_endpoints, list):
            raise InventoryError("signal trace endpoints must be an array")
        by_name = {node.instance: node for node in modules}
        endpoints = []
        keys: set[tuple[str, str]] = set()
        for entry in raw_endpoints:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("instance"), str):
                raise InventoryError("invalid trace endpoint")
            owner = by_name.get(entry["instance"])
            if owner is None or not all(isinstance(entry.get(k), str) for k in ("port", "connection")):
                raise InventoryError("trace endpoint requires a reachable instance, port and connection")
            node = ClockTraceNode.from_mapping({**entry, "module": owner.module}, max_depth=max_depth)
            key = (node.instance, entry["port"])
            if key in keys or (not entry["port"] and entry["connection"]) or node.depth < owner.depth:
                raise InventoryError("duplicate or inconsistent trace endpoint")
            keys.add(key)
            endpoints.append(SourceEndpoint(node.instance, entry["port"], entry["connection"], node.depth, node.path))
        return cls(
            clock_port=clock_port,
            max_depth=max_depth,
            excluded_inputs=tuple(excluded_inputs),
            status=status,
            modules=modules,
            diagnostics=tuple(raw_diagnostics),
            endpoints=tuple(endpoints), is_reset=reset,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            ("reset_port" if self.is_reset else "clock_port"): self.clock_port,
            "max_depth": self.max_depth,
            "excluded_inputs": list(self.excluded_inputs),
            "status": self.status,
            "modules": [module.as_dict() for module in self.modules],
            "diagnostics": list(self.diagnostics),
            **({"endpoints": [e.as_dict() for e in self.endpoints]} if self.endpoints or self.is_reset else {}),
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
    reset_trace: ClockTrace | None = None

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
        if schema_version >= 3 and "clock_trace" not in value:
            raise InventoryError(
                f"instance 'clock_trace' is required by schema_version {schema_version}"
            )
        raw_clock_trace = value.get("clock_trace")
        if raw_clock_trace is None:
            clock_trace = None
        elif isinstance(raw_clock_trace, Mapping):
            clock_trace = ClockTrace.from_mapping(raw_clock_trace, endpoints_required=schema_version >= 4)
        else:
            raise InventoryError("instance 'clock_trace' must be an object or null")
        reset_trace = None
        if schema_version >= 4:
            if "reset_trace" not in value:
                raise InventoryError("instance 'reset_trace' is required by schema_version 4")
            raw_reset = value["reset_trace"]
            if raw_reset is not None:
                if not isinstance(raw_reset, Mapping):
                    raise InventoryError("instance 'reset_trace' must be an object or null")
                reset_trace = ClockTrace.from_mapping(raw_reset, reset=True, endpoints_required=True)
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
            reset_trace=reset_trace,
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
    trace_role: str = "clk"

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "clock_port": self.clock_port,
            "trace_role": self.trace_role,
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
class NameCandidate:
    name: str
    full_name: str
    distance: int

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "full_name": self.full_name, "distance": self.distance}


@dataclass(frozen=True)
class NearestNameMatch:
    status: str
    candidate_count: int = 0
    tied_best_count: int = 0
    ranked: tuple[NameCandidate, ...] = ()
    extra_tied: tuple[NameCandidate, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "metric": "case_sensitive_levenshtein",
            "candidate_count": self.candidate_count,
            "best_distance": self.ranked[0].distance if self.ranked else None,
            "tied_best_count": self.tied_best_count,
            "selected": self.ranked[0].as_dict() if self.status == "matched" else None,
            "top_candidates": [item.as_dict() for item in self.ranked],
            **({"extra_tied_candidates": [item.as_dict() for item in self.extra_tied]} if self.extra_tied else {}),
        }


@dataclass(frozen=True)
class InstanceItemMatch:
    query: str
    rule: str
    paths: tuple[str, ...] = ()
    added_count: int = 0
    nearest: NearestNameMatch | None = None

    @property
    def status(self) -> str:
        if self.nearest is not None:
            return self.nearest.status
        return "matched" if self.paths else "position_not_found"

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query, "rule": self.rule, "status": self.status,
            "matched_paths": list(self.paths), "added_count": self.added_count,
            **(self.nearest.as_dict() if self.nearest is not None else {}),
        }


@dataclass(frozen=True)
class RowResult:
    spec: SpecRow
    instances: tuple[ActualInstance, ...]
    findings: tuple[Finding, ...]
    module_rule: ModuleRule | None = None
    step_evaluations: tuple[InstanceStepEvaluation, ...] = ()
    crg_evaluations: tuple[CrgTraceEvaluation, ...] = ()
    source_signals: tuple[SourceSignalCheck, ...] = ()
    nearest_match: NearestNameMatch | None = None
    item_matches: tuple[InstanceItemMatch, ...] = ()
    suffix_regex: str | None = None
    route_field_used: str = ""
    primary_attempts: tuple[InstanceItemMatch, ...] = ()

    @property
    def source_signal_status(self) -> str:
        statuses = {s.status for s in self.source_signals}
        if statuses & {"invalid_start", "mismatch"}:
            return "fail"
        return "not_run" if not statuses else "pass" if statuses == {"matched"} else "warning"

    @property
    def route_match(self) -> NearestNameMatch | None:
        """Nearest-name evidence only when one of the route fields actually ran."""
        if self.route_field_used:
            return self.nearest_match
        return None

    @property
    def passed(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    @property
    def effective_step(self) -> int | None:
        if (not self.instances or len(self.step_evaluations) != len(self.instances)
                or any(item.status != "matched" for item in self.item_matches)):
            return None
        contributions = [item.contribution for item in self.step_evaluations]
        if any(value is None for value in contributions):
            return None
        return sum(value for value in contributions if value is not None)


@dataclass(frozen=True)
class CheckReport:
    rows: tuple[RowResult, ...]
    global_findings: tuple[Finding, ...] = ()
    skipped_rows: tuple[SkippedRow, ...] = ()

    @property
    def partial_count(self) -> int:
        return sum(r.passed and not r.spec.rs_inst_items for r in self.rows)

    @property
    def status(self) -> str:
        return ("FAIL" if not self.passed else "SKIPPED" if not self.rows else
                "PARTIAL" if self.partial_count else "PASS")

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
