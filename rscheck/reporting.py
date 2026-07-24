from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .model import CheckReport, Finding, OutputError, RowResult


def _row_to_dict(row: RowResult) -> dict[str, Any]:
    evaluations = {item.instance: item for item in row.step_evaluations}
    matched_instances = []
    for instance in row.instances:
        evaluation = evaluations.get(instance.full_name)
        matched_instances.append(
            {
                "name": instance.name,
                "full_name": instance.full_name,
                "module": instance.module,
                "file": instance.file,
                "line": instance.line,
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
                "step_evaluation": evaluation.as_dict() if evaluation else None,
            }
        )
    return {
        "spec": row.spec.as_dict(),
        "passed": row.passed,
        "module_rule": row.module_rule.as_dict() if row.module_rule else None,
        "step_check": {
            "expected": row.spec.step,
            "physical_instances": len(row.instances),
            "effective_step": row.effective_step,
            "contributions": [item.as_dict() for item in row.step_evaluations],
        },
        "matched_instances": matched_instances,
        "findings": [item.as_dict() for item in row.findings],
    }


def report_to_dict(report: CheckReport) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "summary": {
            "passed": report.passed,
            "rows": len(report.rows),
            "passed_rows": sum(row.passed for row in report.rows),
            "failed_rows": sum(not row.passed for row in report.rows),
            "errors": report.error_count,
            "warnings": report.warning_count,
        },
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


def _finding_row(
    finding: Finding, passed: bool, row: RowResult | None = None
) -> dict[str, Any]:
    return {
        "status": "PASS" if passed else finding.severity.upper(),
        "row": finding.row_number if finding.row_number is not None else "",
        "position": finding.position,
        "RS_inst": finding.rs_inst,
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
    ]
    try:
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            for finding in report.global_findings:
                writer.writerow(_finding_row(finding, False))
            for row in report.rows:
                if not row.findings:
                    writer.writerow(
                        {
                            "status": "PASS",
                            "row": row.spec.row_number,
                            "position": row.spec.position,
                            "RS_module": row.spec.rs_module,
                            "RS_inst": row.spec.rs_inst,
                            "RS_CFG_EN": row.spec.rs_cfg_en,
                            **_step_csv_fields(row),
                            "instance": ", ".join(item.full_name for item in row.instances),
                            "code": "",
                            "message": "all checks passed",
                            "expected": "",
                            "actual": "",
                        }
                    )
                else:
                    for finding in row.findings:
                        writer.writerow(_finding_row(finding, False, row))
    except OSError as exc:
        raise OutputError(f"cannot write CSV report {output}: {exc}") from exc
    return output


def format_console_report(report: CheckReport) -> str:
    lines = [
        (
            f"RESULT: {'PASS' if report.passed else 'FAIL'} | rows={len(report.rows)} "
            f"errors={report.error_count} warnings={report.warning_count}"
        )
    ]
    for finding in report.global_findings:
        lines.append(f"[{finding.severity.upper()}] {finding.code}: {finding.message}")
    for row in report.rows:
        effective_step = "?" if row.effective_step is None else str(row.effective_step)
        lines.append(
            f"[{'PASS' if row.passed else 'FAIL'}] row {row.spec.row_number} "
            f"{row.spec.intf_type} | {row.spec.position} / {row.spec.rs_inst} "
            f"physical={len(row.instances)} effective={effective_step} "
            f"expected={row.spec.step} "
            f"RS_CFG_EN={row.spec.rs_cfg_en or '<blank>'}"
        )
        for finding in row.findings:
            lines.append(f"  [{finding.severity.upper()}] {finding.code}: {finding.message}")
    return "\n".join(lines)
