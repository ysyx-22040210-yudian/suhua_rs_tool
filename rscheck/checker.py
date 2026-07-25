from __future__ import annotations

import re
from typing import Iterable, Mapping

from .model import (
    ActualInstance,
    CheckReport,
    ConfigError,
    Finding,
    InstanceStepEvaluation,
    Inventory,
    ModuleRule,
    ParameterEvaluation,
    RowResult,
    RtlConfig,
    SpecRow,
)


_FAKE_GATING_LABEL = "假门控"


def _leaf_name(name: str) -> str:
    value = name.strip().rstrip(".")
    return value.rsplit(".", 1)[-1]


def _signal_matches(expected: str, actual: str, position: str, allow_leaf: bool) -> bool:
    expected = "".join(expected.split())
    actual = "".join(actual.split())
    if expected == actual:
        return True
    if actual == f"{position}.{expected}":
        return True
    return allow_leaf and _leaf_name(expected) == _leaf_name(actual)


def _source_matches(expected: str, instance: str, module: str, mode: str) -> bool:
    expected = expected.strip()
    if mode in {"module", "module_or_instance"} and expected == module:
        return True
    if mode in {"instance", "module_or_instance"}:
        return expected == instance or expected == _leaf_name(instance)
    return False


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
    expected = _FAKE_GATING_LABEL if rule.has_rs_cfg_en else ""
    if spec.rs_cfg_en == expected:
        return []
    return [
        _row_finding(
            spec,
            "RS_CFG_EN_LABEL_MISMATCH",
            (
                "module rule requires Excel RS_CFG_EN to be marked as fake gating"
                if rule.has_rs_cfg_en
                else "module rule declares no RS_CFG_EN parameter, so Excel must be blank"
            ),
            expected=expected,
            actual=spec.rs_cfg_en,
        )
    ]


def _check_rs_cfg_en_instance(
    spec: SpecRow, instance: ActualInstance, rule: ModuleRule
) -> list[Finding]:
    findings: list[Finding] = []
    parameter_name = "RS_CFG_EN"
    parameter_exists = parameter_name in instance.parameters
    if not rule.has_rs_cfg_en:
        if parameter_exists:
            findings.append(
                _row_finding(
                    spec,
                    "RS_CFG_EN_PARAMETER_UNEXPECTED",
                    f"{instance.full_name}: RTL has RS_CFG_EN but the module rule declares none",
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
                f"{instance.full_name}: module rule requires RS_CFG_EN but RTL parameter is missing",
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
                f"{instance.full_name}: RS_CFG_EN parameter value could not be resolved",
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
                f"{instance.full_name}: RS_CFG_EN parameter is not zero",
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
    spec: SpecRow,
    instances: Iterable[ActualInstance],
    suffix: re.Pattern[str],
) -> tuple[
    list[tuple[ActualInstance, re.Match[str] | None]],
    list[ActualInstance],
]:
    valid: list[tuple[ActualInstance, re.Match[str] | None]] = []
    invalid = []
    for instance in instances:
        if not instance.name.startswith(spec.rs_inst):
            continue
        remainder = instance.name[len(spec.rs_inst) :]
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


def _check_ports_and_sources(
    spec: SpecRow,
    instance: ActualInstance,
    config: RtlConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    for role, port_name, expected_connection in (
        ("CLK", config.clk_port, spec.clk),
        ("RST", config.rst_port, spec.rst),
    ):
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
        if "Operation" in port.object_type:
            findings.append(
                _row_finding(
                    spec,
                    "UNSUPPORTED_CONNECTION",
                    f"{instance.full_name}: {role.lower()} is connected through an unsupported expression",
                    instance=instance.full_name,
                    expected=expected_connection,
                    actual={"connection": port.connection, "type": port.object_type},
                )
            )
            continue
        if not _signal_matches(
            expected_connection,
            port.connection,
            spec.position,
            config.allow_leaf_signal_match,
        ):
            findings.append(
                _row_finding(
                    spec,
                    f"{role}_CONNECTION_MISMATCH",
                    f"{instance.full_name}: {role.lower()} connection does not match",
                    instance=instance.full_name,
                    expected=expected_connection,
                    actual=port.connection,
                )
            )

    unique_sources = {
        (source.instance, source.module): source for source in instance.clk_sources
    }
    sources = list(unique_sources.values())
    if not sources:
        findings.append(
            _row_finding(
                spec,
                "CRG_SOURCE_UNRESOLVED",
                f"{instance.full_name}: no upstream module source was resolved for clk",
                instance=instance.full_name,
                expected=spec.crg_source,
                actual=[],
            )
        )
    elif len(sources) > 1:
        findings.append(
            _row_finding(
                spec,
                "MULTIPLE_CLK_SOURCES",
                f"{instance.full_name}: clk has multiple upstream module sources",
                instance=instance.full_name,
                expected="one unique module source",
                actual=[
                    {"instance": source.instance, "module": source.module}
                    for source in sources
                ],
            )
        )
    elif not any(
        _source_matches(spec.crg_source, source.instance, source.module, config.crg_match)
        for source in sources
    ):
        actual_sources = [
            {"instance": source.instance, "module": source.module} for source in sources
        ]
        findings.append(
            _row_finding(
                spec,
                "CRG_SOURCE_MISMATCH",
                f"{instance.full_name}: clk source does not match CRG_source",
                instance=instance.full_name,
                expected=spec.crg_source,
                actual=actual_sources,
            )
        )
    return findings


def _check_row(
    spec: SpecRow,
    inventory: Inventory,
    config: RtlConfig,
    module_rules: Mapping[str, ModuleRule],
    suffix: re.Pattern[str],
) -> RowResult:
    findings: list[Finding] = []
    rule = module_rules.get(spec.rs_module)
    if rule is None:
        rule = ModuleRule(
            name=spec.rs_module,
            has_rs_cfg_en=True,
            step_parameters=(),
        )
    findings.extend(_check_rs_cfg_en_label(spec, rule))

    position = inventory.positions.get(spec.position)
    if position is None or not position.found:
        findings.append(
            _row_finding(
                spec,
                "POSITION_NOT_FOUND",
                f"RTL hierarchy position was not found: {spec.position}",
                expected=spec.position,
                actual=None,
            )
        )
        return RowResult(
            spec=spec,
            instances=(),
            findings=tuple(findings),
            module_rule=rule,
        )

    matches, invalid_suffix = _match_group(spec, position.instances, suffix)
    for instance in invalid_suffix:
        findings.append(
            _row_finding(
                spec,
                "INSTANCE_SUFFIX_INVALID",
                f"{instance.name}: suffix after prefix {spec.rs_inst!r} does not match configured pattern",
                severity="warning",
                instance=instance.full_name,
                expected=config.suffix_regex,
                actual=instance.name[len(spec.rs_inst) :],
            )
        )

    matched_instances = tuple(instance for instance, _ in matches)
    if not matched_instances:
        findings.append(
            _row_finding(
                spec,
                "GROUP_NOT_FOUND",
                f"no instance matched RS_inst {spec.rs_inst!r} and the configured suffix rules",
                expected={"RS_inst": spec.rs_inst, "step": spec.step},
                actual=0,
            )
        )

    suffix_matches = [match for _, match in matches if match is not None]
    tags = {match.groupdict().get("tag", "") for match in suffix_matches}
    if len(tags) > 1:
        findings.append(
            _row_finding(
                spec,
                "SUFFIX_TAG_MISMATCH",
                "instances in one group use different suffix tags",
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
                    "numeric stage indices are not the expected contiguous range",
                    expected=expected_indices,
                    actual=sorted(set(indices)),
                )
            )

    step_evaluations: list[InstanceStepEvaluation] = []
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
        findings.extend(_check_ports_and_sources(spec, instance, config))

    if matched_instances:
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
    )


def check_specs(
    specs: Iterable[SpecRow],
    inventory: Inventory,
    config: RtlConfig,
    module_rules: Mapping[str, ModuleRule],
) -> CheckReport:
    spec_list = list(specs)
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
    ]

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
                            f"{instance.full_name} matches multiple RS_inst groups: "
                            + ", ".join(spec.rs_inst for spec in owners)
                        ),
                        position=position_name,
                        instance=instance.full_name,
                        actual=[spec.rs_inst for spec in owners],
                    )
                )
    return CheckReport(rows=rows, global_findings=tuple(global_findings))
