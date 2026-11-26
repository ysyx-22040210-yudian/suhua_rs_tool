from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .match_review import format_match_details
from .model import (
    RS_INST_FUZZY_RULE, RS_INST_GROUP_RULE, RS_INST_LIST_RULE, CheckReport, Finding, OutputError, RowResult,
)


def _instance_match(row: RowResult) -> dict[str, Any]:
    spec = row.spec
    scope = {"position": spec.position, "RS_module": spec.rs_module}
    items = spec.rs_inst_items
    if not items:
        return {"mode": "none", "query": "", "rule": "not_run", "status": "no_selector",
                **scope, "matched_count": 0}
    if row.route_field_used:
        return {"mode": row.route_field_used, "query": spec.rs_route2 if row.route_field_used == "RS_route2" else spec.rs_route,
                "primary_attempts": [item.as_dict() for item in row.primary_attempts],
                **scope, **row.nearest_match.as_dict()}
    if row.suffix_regex is not None:
        scope["suffix_regex"] = row.suffix_regex
    if row.item_matches:
        return {
            "mode": "RS_inst", "query": spec.rs_inst, "rule": RS_INST_LIST_RULE, **scope,
            "route_ignored": spec.has_route, "matched_count": len(row.instances),
            "items": [item.as_dict() for item in row.item_matches],
        }
    return {
        "mode": "RS_inst",
        "query": items[0] if items else spec.rs_inst,
        **scope,
        "rule": RS_INST_FUZZY_RULE if row.nearest_match is not None else RS_INST_GROUP_RULE,
        "route_ignored": spec.has_route,
        "matched_count": len(row.instances),
        **(row.nearest_match.as_dict() if row.nearest_match is not None else
           {"status": "matched" if row.instances else "position_not_found",
            "matched_paths": [i.full_name for i in row.instances]}),
    }


def _crg_source_check(row: RowResult) -> dict[str, Any]:
    status = "not_run"
    if row.crg_evaluations:
        status = (
            "pass"
            if all(item.status == "matched" for item in row.crg_evaluations)
            else "warning"
        )
    return {
        "expected": row.spec.crg_source,
        "status": status,
        "instances": [item.as_dict() for item in row.crg_evaluations],
    }


def _match_record(row: RowResult) -> dict[str, Any]:
    """The small report subset needed for manual review, without clock graphs or ports."""
    return {
        "spec": row.spec.as_dict(), "passed": row.passed, "instance_match": _instance_match(row),
        "step_check": {
            "expected": row.spec.step, "physical_instances": len(row.instances), "effective_step": row.effective_step,
            "contributions": [item.as_dict() for item in row.step_evaluations],
        },
        "matched_instances": [
            {"name": i.name, "full_name": i.full_name, "module": i.module, "file": i.file, "line": i.line}
            for i in row.instances
        ],
        "findings": [item.as_dict() for item in row.findings],
    }


def _row_to_dict(row: RowResult) -> dict[str, Any]:
    record = _match_record(row)
    evaluations = {item["instance"]: item for item in record["step_check"]["contributions"]}
    matched_instances = []
    for instance, identity in zip(row.instances, record["matched_instances"]):
        matched_instances.append(
            {
                **identity,
                "parameters": dict(instance.parameters),
                "ports": {
                    name: {
                        "connection": port.connection,
                        "type": port.object_type,
                    }
                    for name, port in instance.ports.items()
                },
                "clk_sources": [
                    {"instance": source.instance, "module": source.module}
                    for source in instance.clk_sources
                ],
                "clock_trace": (
                    instance.clock_trace.as_dict()
                    if instance.clock_trace is not None
                    else None
                ),
                "reset_trace": instance.reset_trace.as_dict() if instance.reset_trace else None,
                "step_evaluation": evaluations.get(instance.full_name),
            }
        )
    return {
        **record,
        "module_rule": row.module_rule.as_dict() if row.module_rule else None,
        "crg_source_check": _crg_source_check(row),
        "source_signal_checks": [s.as_dict() for s in row.source_signals],
        "source_signal_status": row.source_signal_status,
        "matched_instances": matched_instances,
    }


def report_to_dict(report: CheckReport) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "check_status": report.status,
        "summary": {
            "passed": report.passed,
            "rows": len(report.rows),
            "passed_rows": sum(row.passed for row in report.rows) - report.partial_count,
            "failed_rows": sum(not row.passed for row in report.rows),
            "errors": report.error_count,
            "warnings": report.warning_count,
            **({"skipped_rows": len(report.skipped_rows)} if report.skipped_rows else {}),
            **({"partial_rows": report.partial_count} if report.partial_count else {}),
        },
        "skipped_rows": [r.as_dict() for r in report.skipped_rows],
        "global_findings": [item.as_dict() for item in report.global_findings],
        "rows": [_row_to_dict(row) for row in report.rows],
    }


def write_json_report(report: CheckReport, path: str | Path) -> Path:
    output = Path(path).resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report_to_dict(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise OutputError(f"cannot write JSON report {output}: {exc}") from exc
    return output


def write_match_details(report: CheckReport, path: str | Path) -> Path:
    output = Path(path).expanduser().resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as stream:
            stream.write(f"例化匹配明细（{len(report.rows)} 条 Excel 规格）\n")
            for row in report.rows:
                stream.write("\n" + format_match_details(_match_record(row)) + "\n")
    except OSError as exc:
        raise OutputError(f"cannot write match details {output}: {exc}") from exc
    return output


def _step_csv_fields(row: RowResult | None) -> dict[str, Any]:
    if row is None:
        return {
            "RS_module": "",
            "physical_instances": "",
            "effective_step": "",
            "step_contributions": "",
        }
    return {
        "RS_module": row.spec.rs_module,
        "physical_instances": len(row.instances),
        "effective_step": "" if row.effective_step is None else row.effective_step,
        "step_contributions": json.dumps(
            [item.as_dict() for item in row.step_evaluations], ensure_ascii=False
        ),
    }


def _crg_csv_fields(row: RowResult | None) -> dict[str, Any]:
    if row is None:
        return {
            "crg_trace_status": "",
            "crg_trace_max_depth": "",
            "crg_trace_evidence": "",
        }
    crg_check = _crg_source_check(row)
    return {
        "crg_trace_status": crg_check["status"],
        "crg_trace_max_depth": (
            row.crg_evaluations[0].max_depth if row.crg_evaluations else ""
        ),
        "crg_trace_evidence": json.dumps(
            crg_check["instances"], ensure_ascii=False
        ),
        "source_signal_checks": json.dumps([s.as_dict() for s in row.source_signals], ensure_ascii=False),
        "source_signal_status": row.source_signal_status,
    }


def _finding_row(
    finding: Finding, passed: bool, row: RowResult | None = None, *, match_fields: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "status": "PASS" if passed else finding.severity.upper(),
        "row": finding.row_number if finding.row_number is not None else "",
        "position": finding.position,
        "position_alias": row.spec.position_alias if row is not None else "",
        "CRG_source": row.spec.crg_source if row is not None else "",
        "crg_source_alias": row.spec.crg_source_alias if row is not None else "",
        "RS_inst": finding.rs_inst,
        "RS_route": row.spec.rs_route if row else "",
        "RS_route2": row.spec.rs_route2 if row else "",
        **(match_fields or {}),
        "RS_CFG_EN": finding.rs_cfg_en,
        "instance": finding.instance,
        "code": finding.code,
        "message": finding.message,
        "expected": json.dumps(finding.expected, ensure_ascii=False)
        if finding.expected is not None
        else "",
        "actual": json.dumps(finding.actual, ensure_ascii=False)
        if finding.actual is not None
        else "",
        **_step_csv_fields(row),
        **_crg_csv_fields(row),
    }


def write_csv_report(report: CheckReport, path: str | Path) -> Path:
    output = Path(path).resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"cannot create CSV report directory {output.parent}: {exc}") from exc
    fieldnames = [
        "status",
        "row",
        "position",
        "position_alias",
        "CRG_source",
        "crg_source_alias",
        "crg_trace_status",
        "crg_trace_max_depth",
        "crg_trace_evidence",
        "source_signal_checks",
        "source_signal_status",
        "RS_module",
        "RS_inst",
        "RS_CFG_EN",
        "physical_instances",
        "effective_step",
        "step_contributions",
        "instance",
        "code",
        "message",
        "expected",
        "actual",
        "RS_route",
        "RS_route2",
        "instance_match",
        "match_details",
    ]
    try:
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            for skipped in report.skipped_rows:
                writer.writerow({"status": "SKIPPED", "row": skipped.row, "code": "ROW_SKIPPED",
                                 "message": skipped.describe()})
            for finding in report.global_findings:
                writer.writerow(_finding_row(finding, False))
            for row in report.rows:
                record = _match_record(row)
                match_fields = {"instance_match": json.dumps(record["instance_match"], ensure_ascii=False),
                                "match_details": format_match_details(record)}
                if not row.findings:
                    writer.writerow(
                        {
                            "status": "PARTIAL" if record["instance_match"]["mode"] == "none" else "PASS",
                            "row": row.spec.row_number,
                            "position": row.spec.position,
                            "position_alias": row.spec.position_alias,
                            "CRG_source": row.spec.crg_source,
                            "crg_source_alias": row.spec.crg_source_alias,
                            "RS_module": row.spec.rs_module,
                            "RS_inst": row.spec.rs_inst,
                            "RS_route": row.spec.rs_route,
                            "RS_route2": row.spec.rs_route2,
                            **match_fields,
                            "RS_CFG_EN": row.spec.rs_cfg_en,
                            **_step_csv_fields(row),
                            **_crg_csv_fields(row),
                            "instance": ", ".join(item.full_name for item in row.instances),
                            "code": "",
                            "message": ("无有效例化选择，实例相关检查未执行" if record["instance_match"]["mode"] == "none"
                                        else "all enabled checks passed"),
                            "expected": "",
                            "actual": "",
                        }
                    )
                else:
                    for finding in row.findings:
                        writer.writerow(_finding_row(finding, False, row, match_fields=match_fields))
    except OSError as exc:
        raise OutputError(f"cannot write CSV report {output}: {exc}") from exc
    return output


def format_console_report(report: CheckReport, *, match_details: bool = False) -> str:
    lines = [
        (
            f"RESULT: {report.status} | rows={len(report.rows)} "
            f"errors={report.error_count} warnings={report.warning_count}"
            + (f" skipped={len(report.skipped_rows)}" if report.skipped_rows else "")
            + (f" partial={report.partial_count}" if report.partial_count else "")
        )
    ]
    for finding in report.global_findings:
        lines.append(f"[{finding.severity.upper()}] {finding.code}: {finding.message}")
    for row in report.rows:
        effective_step = "?" if row.effective_step is None else str(row.effective_step)
        position = row.spec.position
        if row.spec.position_alias:
            position = f"{row.spec.position_alias} -> {row.spec.position}"
        crg_source = row.spec.crg_source
        if row.spec.crg_source_alias:
            crg_source = (
                f"{row.spec.crg_source_alias} -> {row.spec.crg_source}"
            )
        crg_trace_status = str(_crg_source_check(row)["status"]).upper()
        row_status = (
            "FAIL"
            if not row.passed
            else "WARNING"
            if any(item.severity == "warning" for item in row.findings)
            else "PARTIAL" if not row.spec.rs_inst_items
            else "PASS"
        )
        lines.append(
            f"[{row_status}] row {row.spec.row_number} "
            f"{row.spec.intf_type} | {position} / {row.spec.selector_label} "
            f"physical={len(row.instances)} effective={effective_step} "
            f"expected={row.spec.step} "
            f"CRG_source={crg_source} "
            f"CRG_trace={crg_trace_status} source_signals={row.source_signal_status.upper()} "
            f"RS_CFG_EN={row.spec.rs_cfg_en or '<blank>'}"
        )
        for s in row.source_signals:
            outputs = "; ".join(f"{e.instance}.{e.port} -> {e.connection or '?'} (depth {e.depth})" for e in s.endpoints)
            lines.append(f"  {s.role} source: {s.instance}.{s.start_port} -> {s.target}; "
                         f"expected={s.expected} status={s.status}; {outputs or 'no proven output connection'}")
        match = row.nearest_match
        if match is not None:
            ranking = ", ".join(f"{item.full_name} ({item.distance})" for item in match.ranked)
            selected = match.ranked[0].full_name if match.status == "matched" else "<none>"
            items = row.spec.rs_inst_items
            selector = row.route_field_used or "RS_inst fuzzy fallback"
            query = row.spec.rs_route2 if selector == "RS_route2" else row.spec.rs_route if selector == "RS_route" else items[0]
            lines.append(
                f"  {selector}: {match.status}; query={query!r} "
                f"module={row.spec.rs_module} "
                f"candidates={match.candidate_count} selected={selected} "
                f"tied_best={match.tied_best_count}; top candidates "
                f"(case-sensitive edit distance): {ranking or '<none>'}"
            )
        for index, item in enumerate(row.item_matches, 1):
            detail = item.status
            if item.nearest is not None:
                detail += f"; candidates={item.nearest.candidate_count}; tied_best={item.nearest.tied_best_count}"
                if item.nearest.ranked:
                    detail += f"; best_distance={item.nearest.ranked[0].distance}"
            lines.append(f"  RS_inst[{index}] {item.query!r}: {item.rule}; {detail}; "
                         f"selected={', '.join(item.paths) or '<none>'}; newly_counted={item.added_count}")
        if row.spec.rs_inst_items and row.spec.rs_route and not row.route_field_used:
            lines.append(f"  RS_inst takes precedence; RS_route {row.spec.rs_route!r} was not used")
        for finding in row.findings:
            lines.append(f"  [{finding.severity.upper()}] {finding.code}: {finding.message}")
        if match_details:
            lines.append(format_match_details(_match_record(row)))
    return "\n".join(lines)
