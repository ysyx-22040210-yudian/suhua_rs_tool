from __future__ import annotations

import heapq
import re
from typing import Iterable, Mapping

from .model import (
    ActualInstance,
    CheckReport,
    ClockTraceNode,
    ConfigError,
    CrgTraceEvaluation,
    Finding,
    InstanceItemMatch,
    InstanceStepEvaluation,
    Inventory,
    MAX_CRG_TRACE_DEPTH,
    ModuleRule,
    ParameterEvaluation,
    NAME_CANDIDATE_LIMIT,
    NAME_TIE_REVIEW_LIMIT,
    NameCandidate,
    NearestNameMatch,
    RS_INST_FUZZY_RULE,
    RS_INST_GROUP_RULE,
    RowResult,
    RtlConfig,
    SpecRow,
    SourceSignalCheck,
    resolve_module_rule,
    skip_reason,
)


_FAKE_GATING_LABEL = "假门控"


# Schema v2 inventories produced before CRG validation was disabled can retain
# failures from the former Netlist clock-source traversal. Those warnings are
# CRG evidence only; other collector warnings must remain fail-closed.
_IGNORED_CRG_WARNING_PREFIXES = (
    "NPI Netlist module driver is missing instance or definition name for clock connection",
    "clock driver traversal exceeded the depth limit",
    "NPI Netlist could not resolve clock connection",
)


def _is_ignored_crg_warning(warning: str) -> bool:
    return warning.strip().startswith(_IGNORED_CRG_WARNING_PREFIXES)


def _leaf_name(name: str) -> str:
    value = name.strip().rstrip(".")
    return value.rsplit(".", 1)[-1]


def source_connection_matches(expected: str, actual: str, parent: str, allow_leaf: bool) -> bool:
    expected = "".join(expected.split())
    actual = "".join(actual.split())
    if expected == actual:
        return True
    if actual == f"{parent}.{expected}":
        return True
    return allow_leaf and _leaf_name(expected) == _leaf_name(actual)


def _row_finding(
    spec: SpecRow,
    code: str,
    message: str,
    *,
    severity: str = "error",
    instance: str = "",
    expected: object = None,
    actual: object = None,
) -> Finding:
    return Finding(
        severity=severity,
        code=code,
        message=message,
        row_number=spec.row_number,
        position=spec.position,
        rs_inst=spec.rs_inst,
        rs_cfg_en=spec.rs_cfg_en,
        instance=instance,
        expected=expected,
        actual=actual,
    )


def parameter_value_state(value: str | None) -> str:
    if value is None:
        return "unresolved"
    compact = value.strip().replace("_", "").lower()
    if not compact or any(character in compact for character in "xz?"):
        return "unresolved"
    if compact == "'0":
        return "zero"
    if compact == "'1":
        return "nonzero"
    plain = re.fullmatch(r"[+-]?(?P<digits>[0-9]+)", compact)
    if plain is not None:
        return (
            "zero"
            if all(character == "0" for character in plain.group("digits"))
            else "nonzero"
        )

    based = re.fullmatch(
        r"(?P<sign>[+-]?)(?:[0-9]+)?'s?(?P<base>[bodh])(?P<digits>[0-9a-f]+)",
        compact,
    )
    if based is None:
        return "unresolved"
    allowed_digits = {
        "b": frozenset("01"),
        "o": frozenset("01234567"),
        "d": frozenset("0123456789"),
        "h": frozenset("0123456789abcdef"),
    }[based.group("base")]
    digits = based.group("digits")
    if any(character not in allowed_digits for character in digits):
        return "unresolved"
    return "zero" if all(character == "0" for character in digits) else "nonzero"


def _check_rs_cfg_en_label(spec: SpecRow, rule: ModuleRule) -> list[Finding]:
    if skip_reason(spec.rs_cfg_en):
        return []
    if not rule.has_rs_cfg_en:
        return []

    expected = _FAKE_GATING_LABEL
    if spec.rs_cfg_en == expected:
        return []
    return [
        _row_finding(
            spec,
            "RS_CFG_EN_LABEL_MISMATCH",
            "module rule requires Excel RS_CFG_EN to be marked as fake gating",
            expected=expected,
            actual=spec.rs_cfg_en,
        )
    ]


def _check_rs_cfg_en_instance(
    spec: SpecRow, instance: ActualInstance, rule: ModuleRule
) -> list[Finding]:
    if skip_reason(spec.rs_cfg_en):
        return []

    findings: list[Finding] = []
    parameter_name = "RS_CRG_EN"
    parameter_exists = parameter_name in instance.parameters
    if not rule.has_rs_cfg_en:
        if parameter_exists:
            findings.append(
                _row_finding(
                    spec,
                    "RS_CFG_EN_PARAMETER_UNEXPECTED",
                    f"{instance.full_name}: RTL has RS_CRG_EN but the module rule declares none",
                    instance=instance.full_name,
                    expected={"has_rs_cfg_en": False},
                    actual={
                        "has_rs_cfg_en": True,
                        "value": instance.parameters[parameter_name],
                    },
                )
            )
        return findings

    if not parameter_exists:
        findings.append(
            _row_finding(
                spec,
                "RS_CFG_EN_PARAMETER_MISSING",
                f"{instance.full_name}: module rule requires RS_CRG_EN but RTL parameter is missing",
                instance=instance.full_name,
                expected={"has_rs_cfg_en": True},
                actual={"has_rs_cfg_en": False},
            )
        )
        return findings

    value = instance.parameters[parameter_name]
    state = parameter_value_state(value)
    if state == "unresolved":
        findings.append(
            _row_finding(
                spec,
                "RS_CFG_EN_VALUE_UNRESOLVED",
                f"{instance.full_name}: RS_CRG_EN parameter value could not be resolved",
                instance=instance.full_name,
                expected="0",
                actual=value,
            )
        )
    elif state != "zero":
        findings.append(
            _row_finding(
                spec,
                "RS_CFG_EN_VALUE_MISMATCH",
                f"{instance.full_name}: RS_CRG_EN parameter is not zero",
                instance=instance.full_name,
                expected="0",
                actual=value,
            )
        )
    return findings


def _evaluate_step_instance(
    spec: SpecRow,
    instance: ActualInstance,
    rule: ModuleRule,
) -> tuple[InstanceStepEvaluation, list[Finding]]:
    findings: list[Finding] = []
    parameters: list[ParameterEvaluation] = []
    for parameter_name in rule.step_parameters:
        present = parameter_name in instance.parameters
        raw_value = instance.parameters.get(parameter_name)
        if not present:
            state = "missing"
            findings.append(
                _row_finding(
                    spec,
                    "STEP_PARAMETER_MISSING",
                    f"{instance.full_name}: step parameter {parameter_name!r} is missing",
                    instance=instance.full_name,
                    expected={"parameter": parameter_name, "present": True},
                    actual={"parameter": parameter_name, "present": False},
                )
            )
        else:
            state = parameter_value_state(raw_value)
            if state == "unresolved":
                findings.append(
                    _row_finding(
                        spec,
                        "STEP_PARAMETER_VALUE_UNRESOLVED",
                        f"{instance.full_name}: step parameter {parameter_name!r} could not be resolved",
                        instance=instance.full_name,
                        expected="a known numeric zero or nonzero value",
                        actual=raw_value,
                    )
                )
        parameters.append(
            ParameterEvaluation(
                name=parameter_name,
                present=present,
                raw_value=raw_value,
                state=state,
            )
        )

    unresolved = any(item.state in {"missing", "unresolved"} for item in parameters)
    if unresolved:
        contribution = None
    elif any(item.state == "zero" for item in parameters):
        contribution = 0
    else:
        contribution = 1
    return (
        InstanceStepEvaluation(
            instance=instance.full_name,
            contribution=contribution,
            parameters=tuple(parameters),
        ),
        findings,
    )


def _match_group(
    query: str,
    instances: Iterable[ActualInstance],
    suffix: re.Pattern[str],
) -> tuple[
    list[tuple[ActualInstance, re.Match[str] | None]],
    list[ActualInstance],
]:
    valid: list[tuple[ActualInstance, re.Match[str] | None]] = []
    invalid = []
    for instance in instances:
        if not instance.name.startswith(query):
            continue
        remainder = instance.name[len(query) :]
        if not remainder:
            valid.append((instance, None))
            continue
        match = suffix.fullmatch(remainder)
        if match is None:
            invalid.append(instance)
        else:
            valid.append((instance, match))

    def sort_key(
        item: tuple[ActualInstance, re.Match[str] | None],
    ) -> tuple[int, int, str]:
        match = item[1]
        if match is None:
            return 0, -1, item[0].name
        value = match.group("index")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
            raise ConfigError(
                "rtl.suffix_regex named group 'index' must match only ASCII digits"
            )
        return 1, int(value), item[0].name

    valid.sort(key=sort_key)
    return valid, invalid


def _name_distance(left: str, right: str) -> int:
    """Unit-cost Levenshtein distance; equal ends do not affect the result."""
    start = 0
    while start < min(len(left), len(right)) and left[start] == right[start]:
        start += 1
    left, right = left[start:], right[start:]
    end = 0
    while end < min(len(left), len(right)) and left[-1 - end] == right[-1 - end]:
        end += 1
    if end:
        left, right = left[:-end], right[:-end]
    if len(left) > len(right):
        left, right = right, left
    if not left:
        return len(right)
    previous = list(range(len(left) + 1))
    for row, letter in enumerate(right, 1):
        current = [row]
        for col, other in enumerate(left, 1):
            current.append(min(
                current[-1] + 1, previous[col] + 1, previous[col - 1] + (letter != other)
            ))
        previous = current
    return previous[-1]


def _match_nearest_name(
    query: str, module: str, instances: Iterable[ActualInstance]
) -> tuple[ActualInstance | None, NearestNameMatch]:
    count = ties = 0
    best: int | None = None

    def scored() -> Iterable[tuple[int, ActualInstance]]:
        nonlocal count, ties, best
        for instance in instances:
            if instance.module != module:
                continue
            distance = _name_distance(query, instance.name)
            count += 1
            if best is None or distance < best:
                best, ties = distance, 1
            elif distance == best:
                ties += 1
            yield distance, instance

    # Count every minimum, even when the tie is wider than the displayed list.
    top = heapq.nsmallest(NAME_TIE_REVIEW_LIMIT, scored(), key=lambda item: (item[0], item[1].full_name))
    ordered = tuple(NameCandidate(i.name, i.full_name, d) for d, i in top[:max(NAME_CANDIDATE_LIMIT, ties)])
    evidence = NearestNameMatch(
        status="matched" if ties == 1 else "ambiguous" if ties else "no_candidates",
        candidate_count=count,
        tied_best_count=ties,
        ranked=ordered[:NAME_CANDIDATE_LIMIT],
        extra_tied=ordered[NAME_CANDIDATE_LIMIT:],
    )
    return (top[0][1] if ties == 1 else None), evidence


def _check_ports(
    spec: SpecRow,
    instance: ActualInstance,
    rule: ModuleRule,
) -> list[Finding]:
    findings: list[Finding] = []
    for role, port_name, expected_connection in (
        ("CLK", rule.clk_port, spec.clk),
        ("RST", rule.rst_port, spec.rst),
    ):
        if skip_reason(expected_connection):
            continue
        port = instance.ports.get(port_name)
        if port is None:
            findings.append(
                _row_finding(
                    spec,
                    f"{role}_PORT_MISSING",
                    f"{instance.full_name}: formal port {port_name!r} is missing",
                    instance=instance.full_name,
                    expected=expected_connection,
                    actual=None,
                )
            )
            continue
        if not port.connection:
            findings.append(
                _row_finding(
                    spec,
                    f"{role}_UNCONNECTED",
                    f"{instance.full_name}: formal port {port_name!r} is unconnected",
                    instance=instance.full_name,
                    expected=expected_connection,
                    actual=None,
                )
            )
            continue
    return findings


def _source_signal(spec: SpecRow, instance: ActualInstance, config: RtlConfig, rule: ModuleRule,
                   version: int, role: str) -> tuple[SourceSignalCheck, Finding | None]:
    clock = role == "clk"
    formal, expected, trace = ((rule.clk_port, spec.clk, instance.clock_trace) if clock else
                               (rule.rst_port, spec.rst, instance.reset_trace))
    target = spec.crg_source.strip(".")
    local = instance.ports.get(formal)
    endpoints = ()
    matching = ()
    diagnostics = trace.diagnostics if trace else ()
    code = ""
    status = "unavailable"
    if local is None or not local.connection:
        status = "invalid_start"  # _check_ports already reports the structural error.
    elif version < 4:
        diagnostics = ("Legacy inventory has no source-output evidence; recollect with the current collector.",)
    elif trace is None or trace.clock_port != formal:
        diagnostics = (f"No trace from configured {role} port {formal!r}.", *diagnostics)
        code = "SOURCE_SIGNAL_UNAVAILABLE"
    else:
        limit = config.crg_trace_max_depth
        endpoints = tuple(e for e in trace.endpoints if e.instance == target and e.depth <= limit)
        # An output's high connection lives in its parent's scope, not at the RS.
        parent = target.rpartition(".")[0]
        matching = tuple(e for e in endpoints if e.port and e.connection and
                         source_connection_matches(expected, e.connection, parent, config.allow_leaf_signal_match))
        reached = any(n.instance == target and n.depth <= limit for n in trace.modules)
        if matching:
            status = "matched"
        elif reached:
            if not endpoints or any(not e.connection for e in endpoints) or trace.status == "unresolved":
                code = "SOURCE_SIGNAL_UNAVAILABLE"
            else:
                status, code = "mismatch", "CONNECTION_MISMATCH"
        elif trace.status == "unresolved":
            code = "SOURCE_TRACE_UNAVAILABLE"
        elif trace.status == "depth_limited" or any(n.depth > limit for n in trace.modules):
            status, code = "depth_limited", "SOURCE_TRACE_DEPTH_LIMIT"
        else:
            status, code = "not_found", "SOURCE_NOT_FOUND"
        # The primary hierarchy trace already reports failure to reach the CRG.
        if (clock or skip_reason(spec.clk)) and not reached:
            code = ""
    check = SourceSignalCheck(instance.full_name, role, formal, target, expected, status, endpoints, matching,
                              diagnostics, config.allow_leaf_signal_match)
    if not code or ((clock or skip_reason(spec.clk)) and code == "SOURCE_SIGNAL_UNAVAILABLE" and (trace is None or trace.clock_port != formal)):
        return check, None
    observed = ", ".join(f"{e.port} -> {e.connection or '?'}" for e in endpoints)
    message = (f"{instance.full_name}.{formal}: {role} source at {target}: expected {expected!r}; "
               + (f"reached {observed}" if observed else f"{status} within {config.crg_trace_max_depth} level(s)"))
    return check, _row_finding(spec, f"{role.upper()}_{code}", message, instance=instance.full_name,
                               severity="error" if status == "mismatch" else "warning",
                               expected=expected, actual=check.as_dict())


def _evaluate_crg_trace(
    spec: SpecRow,
    instance: ActualInstance,
    config: RtlConfig,
    rule: ModuleRule,
    inventory_schema_version: int,
) -> tuple[CrgTraceEvaluation, Finding | None]:
    expected = spec.crg_source.strip(".")
    # With an active reset alone, establish reachability using its own path. If
    # both signal names are opted out, CRG_source still requests reachability.
    reset_only = bool(skip_reason(spec.clk)) and skip_reason(spec.rst) is None
    role = "rst" if reset_only else "clk"
    formal = rule.rst_port if reset_only else rule.clk_port
    trace = instance.reset_trace if reset_only else instance.clock_trace
    matched: ClockTraceNode | None = None
    trace_status = "unavailable"
    diagnostics: tuple[str, ...] = ()
    modules: tuple[ClockTraceNode, ...] = ()
    status = "unavailable"

    if inventory_schema_version == 2 and not reset_only:
        for source in instance.clk_sources:
            source_name = source.instance.strip(".")
            if source_name == expected and source.module:
                matched = ClockTraceNode(
                    instance=source_name,
                    module=source.module,
                    depth=1,
                    path=(source_name,),
                )
                break
        trace_status = "legacy"
    elif trace is None:
        diagnostics = (f"schema_version {inventory_schema_version} instance has no requested {role} trace",)
    elif trace.clock_port != formal:
        trace_status = trace.status
        diagnostics = (
            f"inventory traced formal port {trace.clock_port!r}, "
            f"but module rule requires {formal!r}",
            *trace.diagnostics,
        )
    else:
        trace_status = trace.status
        diagnostics = trace.diagnostics
        modules = tuple(
            module
            for module in trace.modules
            if module.depth <= config.crg_trace_max_depth
        )
        matched = next(
            (
                module
                for module in sorted(
                    modules,
                    key=lambda item: (item.depth, item.instance, item.module, item.path),
                )
                if module.instance.strip(".") == expected
            ),
            None,
        )
        if matched is None:
            configured_limit_truncated = any(
                module.depth > config.crg_trace_max_depth
                for module in trace.modules
            )
            if trace.status == "unresolved":
                status = "unavailable"
            elif (
                trace.status == "depth_limited"
                or configured_limit_truncated
            ):
                status = "depth_limited"
            else:
                status = "not_found"

    if matched is not None:
        status = "matched"

    evaluation = CrgTraceEvaluation(
        instance=instance.full_name,
        clock_port=formal,
        trace_role=role,
        expected=expected,
        max_depth=config.crg_trace_max_depth,
        status=status,
        trace_status=trace_status,
        matched=matched,
        diagnostics=diagnostics,
    )
    if status == "matched":
        return evaluation, None

    if status == "not_found":
        code = "CRG_SOURCE_NOT_FOUND"
        message = (
            f"{instance.full_name}: CRG_source was not found in the complete "
            f"upstream {role} trace"
        )
    elif status == "depth_limited":
        code = "CRG_TRACE_DEPTH_LIMIT"
        message = (
            f"{instance.full_name}: CRG_source was not found before the "
            f"configured trace depth {config.crg_trace_max_depth}"
        )
    else:
        code = "CRG_TRACE_UNAVAILABLE"
        message = (
            f"{instance.full_name}: upstream {role} trace evidence is unavailable"
        )
    return evaluation, _row_finding(
        spec,
        code,
        message,
        severity="warning",
        instance=instance.full_name,
        expected=expected,
        actual={
            "clock_port": formal, "trace_role": role,
            "configured_max_depth": config.crg_trace_max_depth,
            "trace_status": trace_status,
            "reachable_modules": [module.as_dict() for module in modules],
            "diagnostics": list(diagnostics),
        },
    )


def _select_item(
    spec: SpecRow,
    query: str,
    selector: str,
    instances: tuple[ActualInstance, ...],
    config: RtlConfig,
    suffix: re.Pattern[str],
) -> tuple[tuple[ActualInstance, ...], NearestNameMatch | None, list[Finding]]:
    findings: list[Finding] = []
    matches, invalid_suffix = [], []
    if selector == "RS_inst":
        matches, invalid_suffix = _match_group(query, instances, suffix)
    if not matches:
        winner, nearest = _match_nearest_name(query, spec.rs_module, instances)
        if winner is not None:
            return (winner,), nearest, findings
        if nearest.status == "ambiguous":
            code = "RS_INST_AMBIGUOUS" if selector == "RS_inst" else "RS_ROUTE_AMBIGUOUS"
            message = (
                f"{nearest.tied_best_count} instances of {spec.rs_module!r} under {spec.position} "
                f"tie at distance {nearest.ranked[0].distance} for {selector} {query!r}; "
                "specify the exact local RS_inst or narrow position"
            )
        else:
            code = "GROUP_NOT_FOUND" if selector == "RS_inst" else "RS_ROUTE_NO_CANDIDATES"
            message = f"no instance of module {spec.rs_module!r} under {spec.position} for {selector} {query!r}"
        findings.append(_row_finding(spec, code, message, expected=query, actual=nearest.as_dict()))
        return (), nearest, findings
    for instance in invalid_suffix:
        findings.append(
            _row_finding(
                spec,
                "INSTANCE_SUFFIX_INVALID",
                f"{instance.name}: suffix after prefix {query!r} does not match configured pattern",
                severity="warning",
                instance=instance.full_name,
                expected=config.suffix_regex,
                actual=instance.name[len(query) :],
            )
        )

    suffix_matches = [match for _, match in matches if match is not None]
    tags = {match.groupdict().get("tag", "") for match in suffix_matches}
    if len(tags) > 1:
        findings.append(
            _row_finding(
                spec,
                "SUFFIX_TAG_MISMATCH",
                f"instances for RS_inst {query!r} use different suffix tags",
                severity="warning",
                expected="one common tag",
                actual=sorted(tags),
            )
        )

    indices = [int(match.group("index")) for match in suffix_matches]
    if config.require_contiguous_indices and suffix_matches:
        expected_indices = list(
            range(config.index_base, config.index_base + len(suffix_matches))
        )
        if len(tags) != 1 or sorted(indices) != expected_indices:
            findings.append(
                _row_finding(
                    spec,
                    "STAGE_INDEX_MISMATCH",
                    f"numeric stage indices for RS_inst {query!r} are not the expected contiguous range",
                    expected=expected_indices,
                    actual=sorted(set(indices)),
                )
            )

    return tuple(instance for instance, _ in matches), None, findings


def _check_row(
    spec: SpecRow,
    inventory: Inventory,
    config: RtlConfig,
    module_rules: Mapping[str, ModuleRule],
    suffix: re.Pattern[str],
) -> RowResult:
    rule = resolve_module_rule(spec.rs_module, module_rules)
    findings = [*spec.blank_findings, *_check_rs_cfg_en_label(spec, rule)]
    items = spec.rs_inst_items
    is_list = len(items) > 1
    position = inventory.positions.get(spec.position)
    if position is None or not position.found:
        findings.append(_row_finding(spec, "POSITION_NOT_FOUND",
                                     f"RTL hierarchy position was not found: {spec.position}",
                                     expected=spec.position))
        return RowResult(
            spec=spec, instances=(), findings=tuple(findings), module_rule=rule, suffix_regex=config.suffix_regex,
            nearest_match=NearestNameMatch("position_not_found") if not items and spec.has_route else None,
            item_matches=tuple(InstanceItemMatch(q, "not_run") for q in items) if is_list else (),
        )

    # Check suffix/index rules within each selector's group, but check a shared
    # physical instance (and its dynamic step, ports and CRG trace) only once.
    union: dict[str, ActualInstance] = {}
    item_matches: list[InstanceItemMatch] = []
    resolved: dict[str, InstanceItemMatch] = {}
    nearest_match = None
    if not items and not spec.has_route and not any(i.module == spec.rs_module for i in position.instances):
        findings.append(_row_finding(spec, "RS_MODULE_NOT_FOUND",
            f"{spec.position}: no instance of module {spec.rs_module!r} was found", expected=spec.rs_module))
    target_active = "CRG_source" not in spec.ignored_fields
    for query in items or ((spec.rs_route,) if spec.has_route else ()):
        if query in resolved:
            prior = resolved[query]
            item_matches.append(InstanceItemMatch(query, prior.rule, prior.paths, 0, prior.nearest))
            continue
        members, nearest, diagnostics = _select_item(
            spec, query, "RS_inst" if items else "RS_route", position.instances, config, suffix
        )
        findings.extend(diagnostics)
        before = len(union)
        for instance in members:
            union.setdefault(instance.full_name, instance)
        if is_list:
            evidence = InstanceItemMatch(
                query, RS_INST_FUZZY_RULE if nearest else RS_INST_GROUP_RULE,
                tuple(i.full_name for i in members), len(union) - before, nearest,
            )
            resolved[query] = evidence
            item_matches.append(evidence)
        else:
            nearest_match = nearest
    matched_instances = tuple(union.values())
    step_evaluations: list[InstanceStepEvaluation] = []
    crg_evaluations: list[CrgTraceEvaluation] = []
    source_signals: list[SourceSignalCheck] = []
    for instance in matched_instances:
        module_matches = instance.module == spec.rs_module
        if not module_matches:
            findings.append(
                _row_finding(
                    spec,
                    "RS_MODULE_MISMATCH",
                    f"{instance.full_name}: module definition does not match RS_module",
                    instance=instance.full_name,
                    expected=spec.rs_module,
                    actual=instance.module,
                )
            )
        if not module_matches:
            step_evaluations.append(
                InstanceStepEvaluation(
                    instance=instance.full_name,
                    contribution=None,
                )
            )
        else:
            findings.extend(_check_rs_cfg_en_instance(spec, instance, rule))
            evaluation, step_findings = _evaluate_step_instance(spec, instance, rule)
            step_evaluations.append(evaluation)
            findings.extend(step_findings)
            findings.extend(_check_ports(spec, instance, rule))
            for role in ("clk", "rst"):
                if not target_active or skip_reason(getattr(spec, role)):
                    continue
                signal, finding = _source_signal(spec, instance, config, rule, inventory.schema_version, role)
                source_signals.append(signal)
                if finding:
                    findings.append(finding)
        if not target_active:
            continue
        crg_evaluation, crg_finding = _evaluate_crg_trace(
            spec,
            instance,
            config,
            rule,
            inventory.schema_version,
        )
        crg_evaluations.append(crg_evaluation)
        if crg_finding is not None:
            findings.append(crg_finding)

    if matched_instances and all(item.status == "matched" for item in item_matches):
        contributions = [item.contribution for item in step_evaluations]
        if any(value is None for value in contributions):
            findings.append(
                _row_finding(
                    spec,
                    "STEP_CALCULATION_UNRESOLVED",
                    "effective step could not be calculated from all matched instances",
                    expected=spec.step,
                    actual={
                        "physical_instances": len(matched_instances),
                        "effective_step": None,
                        "contributions": {
                            item.instance: item.contribution for item in step_evaluations
                        },
                    },
                )
            )
        else:
            effective_step = sum(
                value for value in contributions if value is not None
            )
            if effective_step != spec.step:
                findings.append(
                    _row_finding(
                        spec,
                        "STEP_MISMATCH",
                        (
                            f"group has {len(matched_instances)} physical instance(s) "
                            f"but contributes {effective_step} effective step(s), "
                            f"expected {spec.step}"
                        ),
                        expected=spec.step,
                        actual={
                            "physical_instances": len(matched_instances),
                            "effective_step": effective_step,
                            "contributions": {
                                item.instance: item.contribution
                                for item in step_evaluations
                            },
                        },
                    )
                )

    return RowResult(
        spec=spec,
        instances=matched_instances,
        findings=tuple(findings),
        module_rule=rule,
        step_evaluations=tuple(step_evaluations),
        crg_evaluations=tuple(crg_evaluations),
        source_signals=tuple(source_signals),
        nearest_match=nearest_match,
        item_matches=tuple(item_matches),
        suffix_regex=config.suffix_regex,
    )


def check_specs(
    specs: Iterable[SpecRow],
    inventory: Inventory,
    config: RtlConfig,
    module_rules: Mapping[str, ModuleRule],
) -> CheckReport:
    spec_list = list(specs)
    if (
        type(config.crg_trace_max_depth) is not int
        or not 1 <= config.crg_trace_max_depth <= MAX_CRG_TRACE_DEPTH
    ):
        raise ConfigError(
            "rtl.crg_trace_max_depth must be an integer between 1 and "
            f"{MAX_CRG_TRACE_DEPTH}"
        )
    try:
        suffix = re.compile(rf"^(?:{config.suffix_regex})$")
    except re.error as exc:
        raise ConfigError(f"invalid rtl.suffix_regex: {exc}") from exc
    rows = tuple(
        _check_row(spec, inventory, config, module_rules, suffix)
        for spec in spec_list
    )
    global_findings: list[Finding] = [
        Finding(severity="error", code="NPI_UNRESOLVED", message=warning)
        for warning in inventory.warnings
        if not _is_ignored_crg_warning(warning)
    ]
    global_findings.extend(
        Finding(severity="warning", code="NPI_LOAD_PARTIAL", message=notice)
        for notice in inventory.notices
    )
    if inventory.schema_version < 4 and any(r.source_signals for r in rows):
        global_findings.append(Finding(severity="warning", code="SOURCE_INVENTORY_LEGACY",
            message="Legacy inventory: clk/rst source signals were NOT verified. Recollect with the current NPI collector (inventory v4)."))

    owners_by_position: dict[str, dict[str, list[SpecRow]]] = {}
    for row in rows:
        position_owners = owners_by_position.setdefault(row.spec.position, {})
        for instance in row.instances:
            position_owners.setdefault(instance.full_name, []).append(row.spec)
    for position_name, instance_owners in owners_by_position.items():
        position = inventory.positions.get(position_name)
        if position is None or not position.found:
            continue
        for instance in position.instances:
            owners = instance_owners.get(instance.full_name, [])
            if len(owners) > 1:
                global_findings.append(
                    Finding(
                        severity="error",
                        code="AMBIGUOUS_GROUP_MATCH",
                        message=(
                            f"{instance.full_name} matches multiple rows: "
                            + ", ".join(f"row {spec.row_number} ({spec.selector_label})" for spec in owners)
                        ),
                        position=position_name,
                        instance=instance.full_name,
                        actual=[spec.selector_label for spec in owners],
                    )
                )
    return CheckReport(rows=rows, global_findings=tuple(global_findings))
