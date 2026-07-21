from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from .model import (
    ActualInstance,
    CheckReport,
    ConfigError,
    Finding,
    Inventory,
    RowResult,
    RtlConfig,
    SpecRow,
)


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
        instance=instance,
        expected=expected,
        actual=actual,
    )


def _match_group(
    spec: SpecRow,
    instances: Iterable[ActualInstance],
    suffix: re.Pattern[str],
) -> tuple[list[tuple[ActualInstance, re.Match[str]]], list[ActualInstance]]:
    valid = []
    invalid = []
    for instance in instances:
        if not instance.name.startswith(spec.rs_inst):
            continue
        remainder = instance.name[len(spec.rs_inst) :]
        match = suffix.fullmatch(remainder)
        if match is None:
            invalid.append(instance)
        else:
            valid.append((instance, match))
    def stage_index(item: tuple[ActualInstance, re.Match[str]]) -> int:
        value = item[1].group("index")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
            raise ConfigError(
                "rtl.suffix_regex named group 'index' must match only ASCII digits"
            )
        return int(value)

    valid.sort(key=lambda item: (stage_index(item), item[0].name))
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
    suffix: re.Pattern[str],
) -> RowResult:
    findings: list[Finding] = []
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
        return RowResult(spec=spec, instances=(), findings=tuple(findings))

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
                f"no instance matched prefix {spec.rs_inst!r} and the configured suffix pattern",
                expected={"prefix": spec.rs_inst, "step": spec.step},
                actual=0,
            )
        )
    elif len(matched_instances) != spec.step:
        findings.append(
            _row_finding(
                spec,
                "STEP_MISMATCH",
                f"group contains {len(matched_instances)} valid instance(s), expected {spec.step}",
                expected=spec.step,
                actual=len(matched_instances),
            )
        )

    tags = {match.groupdict().get("tag", "") for _, match in matches}
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

    indices = [int(match.group("index")) for _, match in matches]
    if config.require_contiguous_indices:
        expected_indices = list(range(config.index_base, config.index_base + spec.step))
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

    for instance in matched_instances:
        if instance.module != spec.rs_module:
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
        findings.extend(_check_ports_and_sources(spec, instance, config))

    return RowResult(
        spec=spec,
        instances=matched_instances,
        findings=tuple(findings),
    )


def check_specs(specs: Iterable[SpecRow], inventory: Inventory, config: RtlConfig) -> CheckReport:
    spec_list = list(specs)
    try:
        suffix = re.compile(rf"^(?:{config.suffix_regex})$")
    except re.error as exc:
        raise ConfigError(f"invalid rtl.suffix_regex: {exc}") from exc
    rows = tuple(_check_row(spec, inventory, config, suffix) for spec in spec_list)
    global_findings: list[Finding] = [
        Finding(severity="error", code="NPI_UNRESOLVED", message=warning)
        for warning in inventory.warnings
    ]

    specs_by_position: dict[str, list[SpecRow]] = defaultdict(list)
    for spec in spec_list:
        specs_by_position[spec.position].append(spec)
    for position_name, position_specs in specs_by_position.items():
        position = inventory.positions.get(position_name)
        if position is None or not position.found:
            continue
        for instance in position.instances:
            owners = []
            for spec in position_specs:
                if not instance.name.startswith(spec.rs_inst):
                    continue
                remainder = instance.name[len(spec.rs_inst) :]
                if suffix.fullmatch(remainder):
                    owners.append(spec)
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
