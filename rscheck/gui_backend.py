from __future__ import annotations

import json
import os
import select
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence, Union

from .checker import parameter_value_state
from .model import (
    CRG_TRACE_EXCLUDED_INPUTS,
    CRG_TRACE_STATUSES,
    FIELD_NAMES,
    MAX_CRG_TRACE_DEPTH,
)


LIVE_SOURCE = "live"
INVENTORY_SOURCE = "inventory"


_POSIX_EXEC_HELPER = """\
import os
import signal
import sys

stdout_fd, stderr_fd, ready_fd = (int(value) for value in sys.argv[1:4])
cwd = sys.argv[4]
argv = sys.argv[5:]
os.dup2(stdout_fd, 1)
os.dup2(stderr_fd, 2)
os.setsid()
os.write(ready_fd, b"1")
for name in os.listdir("/proc/self/fd"):
    descriptor = int(name)
    if descriptor > 2:
        try:
            os.close(descriptor)
        except OSError:
            pass
for signal_name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
    if hasattr(signal, signal_name):
        signal.signal(getattr(signal, signal_name), signal.SIG_DFL)
if cwd:
    os.chdir(cwd)
os.execvpe(argv[0], argv, os.environ)
"""


_POSIX_SPAWN_LOCK = threading.Lock()
_POSIX_LAUNCH_READY_TIMEOUT = 5.0


class GuiInputError(ValueError):
    """A form value cannot be represented by the supported CLI contract."""


class GuiReportError(ValueError):
    """A CLI result is missing or is not a supported report document."""


@dataclass(frozen=True)
class GuiRunRequest:
    excel_path: str
    config_path: str
    columns: Mapping[str, str] = field(default_factory=dict)
    sheet: str = ""
    header_row: str = ""
    data_start_row: str = ""
    validate_headers: bool = False
    crg_trace_max_depth: str = ""
    source_mode: str = LIVE_SOURCE
    collector_path: str = ""
    elab_db_path: str = ""
    inventory_path: str = ""
    npi_lib_dir: str = ""
    npi_timeout: str = ""
    keep_inventory_path: str = ""
    json_report_path: str = ""
    csv_report_path: str = ""


@dataclass(frozen=True)
class LoadedReport:
    summary: Mapping[str, Any]
    global_findings: tuple[Mapping[str, Any], ...]
    rows: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False


def find_project_root() -> Path:
    configured = os.environ.get("RSCHECK_PROJECT_ROOT", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([Path.cwd(), Path(__file__).resolve().parents[1]])
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / "rscheck").is_dir():
            return candidate.resolve()
    return Path.cwd().resolve()


def default_columns() -> dict[str, str]:
    return {name: str(index) for index, name in enumerate(FIELD_NAMES, start=1)}


def resolve_user_path(value: str | Path, *, base: str | Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(base) / path if base is not None else find_project_root() / path
    return path.resolve(strict=False)


def _path_identity(value: str | Path) -> str:
    return os.path.normcase(str(resolve_user_path(value)))


def _validate_output_paths(
    request: GuiRunRequest,
    *,
    json_report: Path,
    csv_report: Path | None,
    keep_inventory: Path | None,
) -> None:
    outputs = {"JSON report": json_report}
    if csv_report is not None:
        outputs["CSV report"] = csv_report
    if keep_inventory is not None:
        outputs["kept inventory"] = keep_inventory

    seen: dict[str, str] = {}
    for label, path in outputs.items():
        identity = _path_identity(path)
        if identity in seen:
            raise GuiInputError(f"{label} must not use the same path as {seen[identity]}")
        seen[identity] = label

    protected = {
        "Excel input": request.excel_path,
        "configuration input": request.config_path,
    }
    if request.source_mode == INVENTORY_SOURCE:
        protected["inventory input"] = request.inventory_path
    elif request.source_mode == LIVE_SOURCE:
        protected["NPI collector"] = request.collector_path
    protected_identities = {
        _path_identity(path): label for label, path in protected.items() if str(path).strip()
    }
    for label, path in outputs.items():
        protected_label = protected_identities.get(_path_identity(path))
        if protected_label:
            raise GuiInputError(f"{label} must not overwrite {protected_label}")

    protected_directories: list[tuple[str, Path]] = []
    if request.source_mode == LIVE_SOURCE and request.elab_db_path.strip():
        protected_directories.append(
            ("the elaborated KDB", resolve_user_path(request.elab_db_path))
        )
    if request.source_mode == LIVE_SOURCE and request.npi_lib_dir.strip():
        protected_directories.append(
            ("the NPI library directory", resolve_user_path(request.npi_lib_dir))
        )
    for protected_label, protected_directory in protected_directories:
        for label, path in outputs.items():
            try:
                path.relative_to(protected_directory)
            except ValueError:
                continue
            raise GuiInputError(f"{label} must not be written inside {protected_label}")


def _required(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise GuiInputError(f"{label} is required")
    return result


def _positive_integer(value: str, label: str, *, optional: bool = False) -> str:
    result = value.strip()
    if optional and not result:
        return ""
    if not result.isascii() or not result.isdecimal() or int(result) < 1:
        raise GuiInputError(f"{label} must be a positive integer")
    return str(int(result))


def _bounded_positive_integer(
    value: str,
    label: str,
    *,
    maximum: int,
    optional: bool = False,
) -> str:
    result = _positive_integer(value, label, optional=optional)
    if result and int(result) > maximum:
        raise GuiInputError(f"{label} must be between 1 and {maximum}")
    return result


def _common_arguments(request: GuiRunRequest) -> list[str]:
    excel_path = _required(request.excel_path, "Excel path")
    config_path = _required(request.config_path, "configuration path")
    missing = [name for name in FIELD_NAMES if name not in request.columns]
    extra = [name for name in request.columns if name not in FIELD_NAMES]
    if missing:
        raise GuiInputError("missing column mappings: " + ", ".join(missing))
    if extra:
        raise GuiInputError("unknown column mappings: " + ", ".join(sorted(extra)))

    columns: dict[str, str] = {}
    reverse: dict[str, list[str]] = {}
    for field_name in FIELD_NAMES:
        column = _positive_integer(request.columns[field_name], f"column {field_name}")
        columns[field_name] = column
        reverse.setdefault(column, []).append(field_name)
    collisions = {column: names for column, names in reverse.items() if len(names) > 1}
    if collisions:
        detail = "; ".join(
            f"column {column}: {', '.join(names)}"
            for column, names in sorted(collisions.items(), key=lambda item: int(item[0]))
        )
        raise GuiInputError("column mappings must be unique; " + detail)

    header_row = _positive_integer(request.header_row, "header row", optional=True)
    data_start_row = _positive_integer(
        request.data_start_row, "data start row", optional=True
    )
    if header_row and data_start_row and int(data_start_row) <= int(header_row):
        raise GuiInputError("data start row must be after the header row")

    sheet = request.sheet
    if sheet and not sheet.strip():
        raise GuiInputError("sheet name must not be blank")
    if sheet.isascii() and sheet.isdecimal() and int(sheet) < 1:
        raise GuiInputError("sheet index must be >= 1")

    arguments = ["--excel", excel_path, "--config", config_path]
    if sheet:
        arguments.extend(["--sheet", sheet])
    if header_row:
        arguments.extend(["--header-row", header_row])
    if data_start_row:
        arguments.extend(["--data-start-row", data_start_row])
    arguments.append("--header-check" if request.validate_headers else "--no-header-check")
    for field_name in FIELD_NAMES:
        arguments.extend(["--column", f"{field_name}={columns[field_name]}"])
    return arguments


def build_validate_command(
    request: GuiRunRequest, *, python_executable: str | None = None
) -> list[str]:
    python = python_executable or sys.executable
    return [python, "-m", "rscheck", "validate", *_common_arguments(request), "--json"]


def build_check_command(
    request: GuiRunRequest,
    *,
    internal_json_report: str | Path | None = None,
    python_executable: str | None = None,
) -> list[str]:
    python = python_executable or sys.executable
    command = [python, "-m", "rscheck", "check", *_common_arguments(request)]
    crg_trace_max_depth = _bounded_positive_integer(
        request.crg_trace_max_depth,
        "CRG trace maximum depth",
        maximum=256,
        optional=True,
    )
    if crg_trace_max_depth:
        command.extend(["--crg-trace-max-depth", crg_trace_max_depth])

    if request.source_mode == LIVE_SOURCE:
        collector = _required(request.collector_path, "NPI collector path")
        elab_db = _required(request.elab_db_path, "Verdi elaborated KDB path")
        if Path(elab_db.rstrip("/\\")).name == "work.lib++":
            raise GuiInputError("work.lib++ is not an elaborated KDB")
        command.extend(["--collector", collector, "--elab-db", elab_db])
        npi_timeout = _positive_integer(
            request.npi_timeout, "NPI timeout", optional=True
        )
        if npi_timeout:
            command.extend(["--npi-timeout", npi_timeout])
        if request.npi_lib_dir.strip():
            command.extend(["--npi-lib-dir", request.npi_lib_dir.strip()])
        keep_inventory = (
            resolve_user_path(request.keep_inventory_path)
            if request.keep_inventory_path.strip()
            else None
        )
    elif request.source_mode == INVENTORY_SOURCE:
        inventory = _required(request.inventory_path, "inventory path")
        command.extend(["--inventory", inventory])
        keep_inventory = None
    else:
        raise GuiInputError(f"unsupported RTL source mode: {request.source_mode!r}")

    raw_report_path = str(internal_json_report or request.json_report_path).strip()
    if not raw_report_path:
        raise GuiInputError("JSON report path is required")
    report_path = resolve_user_path(raw_report_path)
    csv_report = (
        resolve_user_path(request.csv_report_path)
        if request.csv_report_path.strip()
        else None
    )
    _validate_output_paths(
        request,
        json_report=report_path,
        csv_report=csv_report,
        keep_inventory=keep_inventory,
    )
    if keep_inventory is not None:
        command.extend(["--keep-inventory", str(keep_inventory)])
    command.extend(["--json-report", str(report_path)])
    if csv_report is not None:
        command.extend(["--csv-report", str(csv_report)])
    return command


def format_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def load_validation_rows(stdout: str) -> tuple[Mapping[str, Any], ...]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise GuiReportError(
            f"validation output is not JSON: line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, list):
        raise GuiReportError("validation output must be a JSON array")
    rows: list[Mapping[str, Any]] = []
    required = {"row", *FIELD_NAMES}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise GuiReportError(f"validation row {index + 1} must be an object")
        missing = sorted(required - set(item))
        if missing:
            raise GuiReportError(
                f"validation row {index + 1} is missing: {', '.join(missing)}"
            )
        _validate_position_resolution(item, f"validation row {index + 1}")
        _validate_crg_source_resolution(item, f"validation row {index + 1}")
        rows.append(dict(item))
    return tuple(rows)


def _validate_position_resolution(value: Mapping[str, Any], name: str) -> None:
    position = value.get("position")
    if not isinstance(position, str) or not position:
        raise GuiReportError(f"{name}.position must be a non-empty string")
    if "position_alias" not in value:
        return
    if not isinstance(value["position_alias"], str):
        raise GuiReportError(f"{name}.position_alias must be a string")


def _validate_crg_source_resolution(value: Mapping[str, Any], name: str) -> None:
    crg_source = value.get("CRG_source")
    if not isinstance(crg_source, str) or not crg_source:
        raise GuiReportError(f"{name}.CRG_source must be a non-empty string")
    if "crg_source_alias" not in value:
        return
    if not isinstance(value["crg_source_alias"], str):
        raise GuiReportError(f"{name}.crg_source_alias must be a string")


def _mapping_array(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise GuiReportError(f"{name} must be an array of objects")
    return tuple(dict(item) for item in value)


def _validate_finding_severities(
    findings: Sequence[Mapping[str, Any]], name: str
) -> None:
    for index, finding in enumerate(findings, start=1):
        if finding.get("severity") not in {"error", "warning"}:
            raise GuiReportError(
                f"{name} item {index}.severity must be 'error' or 'warning'"
            )


def _validate_v3_row(
    row: Mapping[str, Any],
    spec: Mapping[str, Any],
    matched_instances: Sequence[Mapping[str, Any]],
    row_number: int,
) -> None:
    step = spec.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise GuiReportError(
            f"report row {row_number}.spec.step must be a non-negative integer"
        )

    module_rule = row.get("module_rule")
    step_parameters: list[str] | None = None
    if module_rule is not None:
        if not isinstance(module_rule, Mapping):
            raise GuiReportError(f"report row {row_number}.module_rule must be an object or null")
        name = module_rule.get("name")
        if not isinstance(name, str) or not name:
            raise GuiReportError(f"report row {row_number}.module_rule.name must be a string")
        if name != spec.get("RS_module"):
            raise GuiReportError(
                f"report row {row_number}.module_rule.name does not match RS_module"
            )
        if not isinstance(module_rule.get("has_rs_cfg_en"), bool):
            raise GuiReportError(
                f"report row {row_number}.module_rule.has_rs_cfg_en must be true or false"
            )
        for port_field in ("clk_port", "rst_port"):
            if port_field not in module_rule:
                continue
            port_name = module_rule[port_field]
            if not isinstance(port_name, str) or not port_name.strip():
                raise GuiReportError(
                    f"report row {row_number}.module_rule.{port_field} "
                    "must be a non-empty string"
                )
        step_parameters = module_rule.get("step_parameters")
        if (
            not isinstance(step_parameters, list)
            or not all(
                isinstance(item, str)
                and item
                and not any(character.isspace() for character in item)
                for item in step_parameters
            )
            or len(set(step_parameters)) != len(step_parameters)
        ):
            raise GuiReportError(
                f"report row {row_number}.module_rule.step_parameters is invalid"
            )

    step_check = row.get("step_check")
    if not isinstance(step_check, Mapping):
        raise GuiReportError(f"report row {row_number}.step_check must be an object")
    report_expected = step_check.get("expected")
    if type(report_expected) is not int or report_expected != step:
        raise GuiReportError(
            f"report row {row_number}.step_check.expected does not match spec.step"
        )
    physical_instances = step_check.get("physical_instances")
    if (
        isinstance(physical_instances, bool)
        or not isinstance(physical_instances, int)
        or physical_instances != len(matched_instances)
    ):
        raise GuiReportError(
            f"report row {row_number}.step_check.physical_instances does not match instances"
        )
    contributions = _mapping_array(
        step_check.get("contributions"),
        f"report row {row_number}.step_check.contributions",
    )
    if len(contributions) != len(matched_instances):
        raise GuiReportError(
            f"report row {row_number}.step_check.contributions does not match instances"
        )

    contribution_values: list[int | None] = []
    for index, (contribution, instance) in enumerate(
        zip(contributions, matched_instances), start=1
    ):
        if contribution.get("instance") != instance.get("full_name"):
            raise GuiReportError(
                f"report row {row_number} contribution {index}.instance does not match"
            )
        value = contribution.get("contribution")
        if value is not None and (type(value) is not int or value not in {0, 1}):
            raise GuiReportError(
                f"report row {row_number} contribution {index} must be 0, 1, or null"
            )
        parameter_evaluations = contribution.get("parameters")
        if not isinstance(parameter_evaluations, Mapping):
            raise GuiReportError(
                f"report row {row_number} contribution {index}.parameters must be an object"
            )
        expected_parameter_names = (
            step_parameters
            if step_parameters is not None
            and instance.get("module") == spec.get("RS_module")
            else []
        )
        if set(parameter_evaluations) != set(expected_parameter_names):
            raise GuiReportError(
                f"report row {row_number} contribution {index}.parameters "
                "does not match module_rule.step_parameters"
            )

        evaluation_states: list[str] = []
        instance_parameters = instance.get("parameters")
        for parameter_name in expected_parameter_names:
            evaluation = parameter_evaluations[parameter_name]
            if not isinstance(parameter_name, str) or not parameter_name:
                raise GuiReportError(
                    f"report row {row_number} contribution parameter names must not be empty"
                )
            if not isinstance(evaluation, Mapping):
                raise GuiReportError(
                    f"report row {row_number} contribution parameter {parameter_name!r} must be an object"
                )
            present = evaluation.get("present")
            raw_value = evaluation.get("raw_value")
            state = evaluation.get("state")
            if not isinstance(present, bool):
                raise GuiReportError(
                    f"report row {row_number} contribution parameter present must be boolean"
                )
            if raw_value is not None and not isinstance(raw_value, str):
                raise GuiReportError(
                    f"report row {row_number} contribution parameter raw_value must be string or null"
                )
            if state not in {"zero", "nonzero", "missing", "unresolved"}:
                raise GuiReportError(
                    f"report row {row_number} contribution parameter state is invalid"
                )
            if (state == "missing") != (not present):
                raise GuiReportError(
                    f"report row {row_number} contribution parameter presence is inconsistent"
                )
            actual_present = parameter_name in instance_parameters
            actual_raw_value = instance_parameters.get(parameter_name)
            if present != actual_present or raw_value != actual_raw_value:
                raise GuiReportError(
                    f"report row {row_number} contribution parameter "
                    f"{parameter_name!r} does not match instance parameters"
                )
            expected_state = (
                parameter_value_state(actual_raw_value)
                if actual_present
                else "missing"
            )
            if state != expected_state:
                raise GuiReportError(
                    f"report row {row_number} contribution parameter "
                    f"{parameter_name!r} state does not match its raw_value"
                )
            evaluation_states.append(state)

        if step_parameters is None or instance.get("module") != spec.get("RS_module"):
            expected_contribution = None
        elif any(state in {"missing", "unresolved"} for state in evaluation_states):
            expected_contribution = None
        elif any(state == "zero" for state in evaluation_states):
            expected_contribution = 0
        else:
            expected_contribution = 1
        if value != expected_contribution:
            raise GuiReportError(
                f"report row {row_number} contribution {index} does not match "
                "its parameter states"
            )
        if instance.get("step_evaluation") != contribution:
            raise GuiReportError(
                f"report row {row_number} instance {index}.step_evaluation is inconsistent"
            )
        contribution_values.append(value)

    effective_step = step_check.get("effective_step")
    expected_effective = (
        None
        if not contribution_values or any(value is None for value in contribution_values)
        else sum(value for value in contribution_values if value is not None)
    )
    if effective_step is not None and type(effective_step) is not int:
        raise GuiReportError(
            f"report row {row_number}.step_check.effective_step must be an integer or null"
        )
    if effective_step != expected_effective:
        raise GuiReportError(
            f"report row {row_number}.step_check.effective_step does not match contributions"
        )
    if row.get("passed") and effective_step != step:
        raise GuiReportError(
            f"report row {row_number}.passed is inconsistent with effective_step"
        )


def _validate_trace_node(
    value: Any,
    name: str,
    *,
    max_depth: int,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GuiReportError(f"{name} must be an object")
    instance = value.get("instance")
    module = value.get("module")
    depth = value.get("depth")
    path = value.get("path")
    if not isinstance(instance, str) or not instance:
        raise GuiReportError(f"{name}.instance must be a non-empty string")
    if not isinstance(module, str) or not module:
        raise GuiReportError(f"{name}.module must be a non-empty string")
    if type(depth) is not int or not 1 <= depth <= max_depth:
        raise GuiReportError(
            f"{name}.depth must be an integer between 1 and {max_depth}"
        )
    if not isinstance(path, list) or not all(
        isinstance(item, str) and item for item in path
    ):
        raise GuiReportError(f"{name}.path must be an array of non-empty strings")
    if len(path) != depth or path[-1] != instance:
        raise GuiReportError(
            f"{name}.path length/final instance is inconsistent with depth"
        )
    return value


def _validate_report_clock_trace(value: Any, name: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise GuiReportError(f"{name} must be an object or null")
    clock_port = value.get("clock_port")
    max_depth = value.get("max_depth")
    excluded_inputs = value.get("excluded_inputs")
    status = value.get("status")
    modules = value.get("modules")
    diagnostics = value.get("diagnostics")
    if not isinstance(clock_port, str) or not clock_port.strip():
        raise GuiReportError(f"{name}.clock_port must be a non-empty string")
    if type(max_depth) is not int or not 1 <= max_depth <= MAX_CRG_TRACE_DEPTH:
        raise GuiReportError(
            f"{name}.max_depth must be an integer between 1 and {MAX_CRG_TRACE_DEPTH}"
        )
    if excluded_inputs != list(CRG_TRACE_EXCLUDED_INPUTS):
        raise GuiReportError(
            f"{name}.excluded_inputs must be exactly {list(CRG_TRACE_EXCLUDED_INPUTS)!r}"
        )
    if status not in CRG_TRACE_STATUSES:
        raise GuiReportError(f"{name}.status is invalid")
    if not isinstance(modules, list):
        raise GuiReportError(f"{name}.modules must be an array")
    for index, module in enumerate(modules, start=1):
        _validate_trace_node(
            module,
            f"{name}.modules item {index}",
            max_depth=max_depth,
        )
    if not isinstance(diagnostics, list) or not all(
        isinstance(item, str) for item in diagnostics
    ):
        raise GuiReportError(f"{name}.diagnostics must be an array of strings")
    return value


def _validate_v4_crg_source_check(
    row: Mapping[str, Any],
    spec: Mapping[str, Any],
    matched_instances: Sequence[Mapping[str, Any]],
    row_findings: Sequence[Mapping[str, Any]],
    row_number: int,
) -> None:
    check_name = f"report row {row_number}.crg_source_check"
    check = row.get("crg_source_check")
    if not isinstance(check, Mapping):
        raise GuiReportError(f"{check_name} must be an object")
    expected = check.get("expected")
    spec_expected = str(spec.get("CRG_source", "")).strip(".")
    if not isinstance(expected, str) or not expected:
        raise GuiReportError(f"{check_name}.expected must be a non-empty string")
    if expected != spec_expected:
        raise GuiReportError(f"{check_name}.expected does not match spec.CRG_source")
    status = check.get("status")
    if status not in {"not_run", "pass", "warning"}:
        raise GuiReportError(f"{check_name}.status is invalid")
    evaluations = _mapping_array(check.get("instances"), f"{check_name}.instances")
    if len(evaluations) != len(matched_instances):
        raise GuiReportError(f"{check_name}.instances does not match instances")

    evaluation_statuses: list[str] = []
    for index, (evaluation, instance) in enumerate(
        zip(evaluations, matched_instances), start=1
    ):
        name = f"{check_name}.instances item {index}"
        full_name = instance.get("full_name")
        if evaluation.get("instance") != full_name:
            raise GuiReportError(f"{name}.instance does not match")
        clock_port = evaluation.get("clock_port")
        if not isinstance(clock_port, str) or not clock_port.strip():
            raise GuiReportError(f"{name}.clock_port must be a non-empty string")
        module_rule = row.get("module_rule")
        if isinstance(module_rule, Mapping) and "clk_port" in module_rule:
            if clock_port != module_rule["clk_port"]:
                raise GuiReportError(f"{name}.clock_port does not match module_rule")
        if evaluation.get("expected") != expected:
            raise GuiReportError(f"{name}.expected does not match CRG_source")
        max_depth = evaluation.get("max_depth")
        if type(max_depth) is not int or not 1 <= max_depth <= MAX_CRG_TRACE_DEPTH:
            raise GuiReportError(
                f"{name}.max_depth must be an integer between 1 and {MAX_CRG_TRACE_DEPTH}"
            )
        evaluation_status = evaluation.get("status")
        if evaluation_status not in {
            "matched",
            "not_found",
            "depth_limited",
            "unavailable",
        }:
            raise GuiReportError(f"{name}.status is invalid")
        trace_status = evaluation.get("trace_status")
        if trace_status not in {*CRG_TRACE_STATUSES, "legacy", "unavailable"}:
            raise GuiReportError(f"{name}.trace_status is invalid")
        diagnostics = evaluation.get("diagnostics")
        if not isinstance(diagnostics, list) or not all(
            isinstance(item, str) for item in diagnostics
        ):
            raise GuiReportError(f"{name}.diagnostics must be an array of strings")

        trace = _validate_report_clock_trace(
            instance.get("clock_trace"),
            f"report row {row_number} instance {index}.clock_trace",
        )
        if trace is not None and trace_status != trace.get("status"):
            raise GuiReportError(f"{name}.trace_status does not match clock_trace")
        if trace is None and trace_status not in {"legacy", "unavailable"}:
            raise GuiReportError(
                f"{name}.trace_status requires instance clock_trace evidence"
            )

        matched = evaluation.get("matched")
        matched_node: Mapping[str, Any] | None = None
        if matched is not None:
            matched_node = _validate_trace_node(
                matched,
                f"{name}.matched",
                max_depth=max_depth,
            )
            if str(matched_node.get("instance", "")).strip(".") != expected:
                raise GuiReportError(
                    f"{name}.matched.instance does not match CRG_source"
                )

        derived_matched: Mapping[str, Any] | None = None
        if trace is None:
            legacy_sources = instance.get("clk_sources")
            legacy_source = next(
                (
                    source
                    for source in legacy_sources
                    if isinstance(source, Mapping)
                    and str(source.get("instance", "")).strip(".") == expected
                    and isinstance(source.get("module"), str)
                    and bool(source.get("module"))
                ),
                None,
            ) if isinstance(legacy_sources, list) else None
            if trace_status == "legacy" and legacy_source is not None:
                derived_status = "matched"
                derived_matched = {
                    "instance": expected,
                    "module": legacy_source["module"],
                    "depth": 1,
                    "path": [expected],
                }
            else:
                derived_status = "unavailable"
        elif trace.get("clock_port") != clock_port:
            derived_status = "unavailable"
        else:
            reachable_modules = [
                module
                for module in trace.get("modules", [])
                if module["depth"] <= max_depth
            ]
            matching_modules = sorted(
                (
                    module
                    for module in reachable_modules
                    if str(module.get("instance", "")).strip(".") == expected
                ),
                key=lambda module: (
                    module["depth"],
                    module["instance"],
                    module["module"],
                    tuple(module["path"]),
                ),
            )
            if matching_modules:
                derived_status = "matched"
                derived_matched = matching_modules[0]
            elif trace.get("status") == "unresolved":
                derived_status = "unavailable"
            elif trace.get("status") == "depth_limited" or any(
                module["depth"] > max_depth
                for module in trace.get("modules", [])
            ):
                derived_status = "depth_limited"
            else:
                derived_status = "not_found"

        if evaluation_status != derived_status:
            raise GuiReportError(f"{name}.status does not match clock trace evidence")
        if matched_node != derived_matched:
            raise GuiReportError(f"{name}.matched does not match clock trace evidence")

        if trace is not None:
            expected_diagnostics = list(trace.get("diagnostics", []))
            if trace.get("clock_port") != clock_port:
                expected_diagnostics.insert(
                    0,
                    f"inventory traced formal port {trace.get('clock_port')!r}, "
                    f"but module rule requires {clock_port!r}",
                )
            if diagnostics != expected_diagnostics:
                raise GuiReportError(
                    f"{name}.diagnostics does not match clock trace evidence"
                )
        elif trace_status == "legacy" and diagnostics:
            raise GuiReportError(f"{name}.legacy diagnostics must be empty")
        evaluation_statuses.append(evaluation_status)

    expected_status = (
        "not_run"
        if not evaluation_statuses
        else "pass"
        if all(item == "matched" for item in evaluation_statuses)
        else "warning"
    )
    if status != expected_status:
        raise GuiReportError(f"{check_name}.status does not match instance evaluations")

    warning_codes = {
        "not_found": "CRG_SOURCE_NOT_FOUND",
        "depth_limited": "CRG_TRACE_DEPTH_LIMIT",
        "unavailable": "CRG_TRACE_UNAVAILABLE",
    }
    expected_findings = sorted(
        (warning_codes[evaluation["status"]], evaluation["instance"])
        for evaluation in evaluations
        if evaluation["status"] != "matched"
    )
    if any(
        finding.get("severity") != "warning"
        for finding in row_findings
        if finding.get("code") in set(warning_codes.values())
    ):
        raise GuiReportError(f"{check_name} findings must have warning severity")
    actual_findings = sorted(
        (str(finding.get("code")), str(finding.get("instance")))
        for finding in row_findings
        if finding.get("code") in set(warning_codes.values())
    )
    if actual_findings != expected_findings:
        raise GuiReportError(
            f"{check_name}.instances do not match CRG warning findings"
        )


def load_report(path: str | Path) -> LoadedReport:
    report_path = Path(path)
    try:
        value = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GuiReportError(f"JSON report was not created: {report_path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise GuiReportError(f"cannot read JSON report {report_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GuiReportError(
            f"invalid JSON report {report_path}: line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, Mapping):
        raise GuiReportError("JSON report root must be an object")
    schema_version = value.get("schema_version")
    if type(schema_version) is not int or schema_version not in {2, 3, 4}:
        raise GuiReportError("unsupported JSON report schema_version")
    summary = value.get("summary")
    if not isinstance(summary, Mapping):
        raise GuiReportError("JSON report summary must be an object")
    for name in ("passed", "rows", "passed_rows", "failed_rows", "errors", "warnings"):
        if name not in summary:
            raise GuiReportError(f"JSON report summary is missing {name!r}")
    if not isinstance(summary["passed"], bool):
        raise GuiReportError("JSON report summary.passed must be true or false")
    for name in ("rows", "passed_rows", "failed_rows", "errors", "warnings"):
        if (
            isinstance(summary[name], bool)
            or not isinstance(summary[name], int)
            or summary[name] < 0
        ):
            raise GuiReportError(
                f"JSON report summary.{name} must be a non-negative integer"
            )

    global_findings = _mapping_array(value.get("global_findings"), "global_findings")
    _validate_finding_severities(global_findings, "global_findings")
    rows = _mapping_array(value.get("rows"), "rows")
    if summary["rows"] != len(rows):
        raise GuiReportError("JSON report summary.rows does not match rows length")
    if summary["passed_rows"] + summary["failed_rows"] != summary["rows"]:
        raise GuiReportError(
            "JSON report passed_rows and failed_rows do not add up to rows"
        )
    all_findings = list(global_findings)
    actual_passed_rows = 0
    for index, row in enumerate(rows):
        spec = row.get("spec")
        if not isinstance(spec, Mapping):
            raise GuiReportError(f"report row {index + 1}.spec must be an object")
        missing = [name for name in ("row",) + FIELD_NAMES if name not in spec]
        if missing:
            raise GuiReportError(
                f"report row {index + 1}.spec is missing: {', '.join(missing)}"
            )
        if not isinstance(spec["RS_CFG_EN"], str):
            raise GuiReportError(
                f"report row {index + 1}.spec.RS_CFG_EN must be a string"
            )
        _validate_position_resolution(spec, f"report row {index + 1}.spec")
        _validate_crg_source_resolution(spec, f"report row {index + 1}.spec")
        if not isinstance(row.get("passed"), bool):
            raise GuiReportError(f"report row {index + 1}.passed must be true or false")
        actual_passed_rows += int(row["passed"])
        matched_instances = _mapping_array(
            row.get("matched_instances"),
            f"report row {index + 1}.matched_instances",
        )
        for instance_index, instance in enumerate(matched_instances, start=1):
            parameters = instance.get("parameters")
            if not isinstance(parameters, Mapping):
                raise GuiReportError(
                    f"report row {index + 1} instance {instance_index}.parameters "
                    "must be an object"
                )
            for name, parameter_value in parameters.items():
                if not isinstance(name, str) or not name:
                    raise GuiReportError(
                        f"report row {index + 1} instance {instance_index} "
                        "parameter names must not be empty"
                    )
                if parameter_value is not None and not isinstance(parameter_value, str):
                    raise GuiReportError(
                        f"report row {index + 1} instance {instance_index} "
                        f"parameter {name!r} must be a string or null"
                    )
        if schema_version in {3, 4}:
            _validate_v3_row(row, spec, matched_instances, index + 1)
        row_findings = _mapping_array(
            row.get("findings"), f"report row {index + 1}.findings"
        )
        _validate_finding_severities(
            row_findings, f"report row {index + 1}.findings"
        )
        if schema_version == 4:
            _validate_v4_crg_source_check(
                row, spec, matched_instances, row_findings, index + 1
            )
        row_has_error = any(item.get("severity") == "error" for item in row_findings)
        if row["passed"] != (not row_has_error):
            raise GuiReportError(
                f"report row {index + 1}.passed does not match its error findings"
            )
        all_findings.extend(row_findings)
    if actual_passed_rows != summary["passed_rows"]:
        raise GuiReportError("JSON report passed_rows does not match row results")
    actual_errors = sum(item.get("severity") == "error" for item in all_findings)
    actual_warnings = sum(item.get("severity") == "warning" for item in all_findings)
    if actual_errors != summary["errors"] or actual_warnings != summary["warnings"]:
        raise GuiReportError("JSON report finding counts do not match summary")
    if summary["passed"] != (actual_errors == 0 and summary["failed_rows"] == 0):
        raise GuiReportError("JSON report passed state does not match row findings")
    return LoadedReport(
        summary=dict(summary),
        global_findings=global_findings,
        rows=rows,
        raw=dict(value),
    )


class _PosixSpawnProcess:
    def __init__(
        self,
        pid: int,
        stdout_fd: int,
        stderr_fd: int,
        args: Sequence[str],
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.args = tuple(args)
        self._stdout_fd = stdout_fd
        self._stderr_fd = stderr_fd
        self._wait_lock = threading.Lock()

    @classmethod
    def start(
        cls,
        command: Sequence[str],
        *,
        cwd: str | Path | None,
        environment: Mapping[str, str],
    ) -> _PosixSpawnProcess:
        with _POSIX_SPAWN_LOCK:
            all_descriptors: list[int] = []
            try:
                stdout_read, stdout_write = os.pipe()
                all_descriptors.extend((stdout_read, stdout_write))
                stderr_read, stderr_write = os.pipe()
                all_descriptors.extend((stderr_read, stderr_write))
                ready_read, ready_write = os.pipe()
                all_descriptors.extend((ready_read, ready_write))
                launcher = [
                    str(Path(sys.executable).resolve()),
                    "-X",
                    "utf8",
                    "-I",
                    "-S",
                    "-c",
                    _POSIX_EXEC_HELPER,
                    str(stdout_write),
                    str(stderr_write),
                    str(ready_write),
                    str(cwd) if cwd is not None else "",
                    *command,
                ]
                write_descriptors = (stdout_write, stderr_write, ready_write)
                for descriptor in write_descriptors:
                    os.set_inheritable(descriptor, True)
                # No optional posix_spawn arguments: glibc 2.17 uses vfork only
                # when flags are zero and file_actions is null.
                pid = os.posix_spawn(
                    launcher[0], launcher, dict(environment)
                )
            except BaseException:
                for descriptor in all_descriptors:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                raise
            for descriptor in write_descriptors:
                os.close(descriptor)

        process = cls(pid, stdout_read, stderr_read, command)
        try:
            ready, _writable, _errors = select.select(
                [ready_read], [], [], _POSIX_LAUNCH_READY_TIMEOUT
            )
            marker = os.read(ready_read, 1) if ready else b""
        except BaseException:
            process._force_kill()
            process.communicate()
            raise
        finally:
            os.close(ready_read)
        if marker == b"1":
            return process
        process._force_kill()
        _stdout, stderr = process.communicate()
        detail = stderr.strip() or "launcher readiness timeout"
        raise RuntimeError(
            f"Linux GUI command launcher failed before exec: {detail}"
        )

    @staticmethod
    def _exit_code(status: int) -> int:
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        raise RuntimeError(f"unexpected child process status: {status}")

    def poll(self) -> int | None:
        with self._wait_lock:
            if self.returncode is not None:
                return self.returncode
            waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
            if waited_pid == 0:
                return None
            self.returncode = self._exit_code(status)
            return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            with self._wait_lock:
                if self.returncode is None:
                    _waited_pid, status = os.waitpid(self.pid, 0)
                    self.returncode = self._exit_code(status)
                return self.returncode
        deadline = time.monotonic() + timeout
        while True:
            returncode = self.poll()
            if returncode is not None:
                return returncode
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(0.01)

    def terminate(self) -> None:
        os.kill(self.pid, signal.SIGTERM)

    def kill(self) -> None:
        os.kill(self.pid, signal.SIGKILL)

    def _force_kill(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except OSError:
            if self.poll() is None:
                try:
                    self.kill()
                except ProcessLookupError:
                    pass

    def communicate(self) -> tuple[str, str]:
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        read_errors: list[BaseException] = []

        def read_pipe(descriptor: int, chunks: list[bytes]) -> None:
            try:
                with os.fdopen(descriptor, "rb") as stream:
                    chunks.append(stream.read())
            except BaseException as exc:
                read_errors.append(exc)

        readers = (
            threading.Thread(
                target=read_pipe,
                args=(self._stdout_fd, stdout_chunks),
                name="rscheck-gui-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=read_pipe,
                args=(self._stderr_fd, stderr_chunks),
                name="rscheck-gui-stderr",
                daemon=True,
            ),
        )
        started_readers: list[threading.Thread] = []
        try:
            for reader in readers:
                reader.start()
                started_readers.append(reader)
        except BaseException:
            self._force_kill()
            pipe_descriptors = (self._stdout_fd, self._stderr_fd)
            for descriptor in pipe_descriptors[len(started_readers) :]:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            self.wait()
            for reader in started_readers:
                reader.join()
            raise
        self.wait()
        for reader in started_readers:
            reader.join()
        if read_errors:
            raise read_errors[0]
        return (
            b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        )


_Process = Union[subprocess.Popen, _PosixSpawnProcess]


class ProcessController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: _Process | None = None
        self._process_group_id: int | None = None
        self._starting = False
        self._cancel_requested = False
        self._termination_started = False
        self._termination_done: threading.Event | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._starting or self._process is not None

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        with self._lock:
            if self._starting or self._process is not None:
                raise RuntimeError("another GUI command is already running")
            self._starting = True
            self._cancel_requested = False
            self._termination_started = False
            termination_done = threading.Event()
            self._termination_done = termination_done
            self._process_group_id = None

        child_environment = os.environ.copy()
        if environment:
            child_environment.update(environment)
        child_environment["PYTHONUTF8"] = "1"
        child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            process = self._start_process(command, cwd, child_environment)
        except Exception:
            with self._lock:
                self._starting = False
                self._cancel_requested = False
                self._termination_done = None
            raise
        termination_target: tuple[_Process, int | None, threading.Event] | None
        with self._lock:
            self._process = process
            # Both POSIX launch paths make the child's PID the stable process
            # group ID even if the group leader exits first.
            self._process_group_id = process.pid if os.name != "nt" else None
            self._starting = False
            if self._cancel_requested and not self._termination_started:
                self._termination_started = True
                termination_target = (
                    process,
                    self._process_group_id,
                    termination_done,
                )
            else:
                termination_target = None
        if termination_target is not None:
            self._request_process_stop(*termination_target)
        try:
            stdout, stderr = process.communicate()
            with self._lock:
                cancelled = self._cancel_requested
                termination_started = self._termination_started
            if cancelled and termination_started:
                termination_done.wait()
            return ProcessResult(
                command=tuple(command),
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                cancelled=cancelled,
            )
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
                    self._process_group_id = None
                    self._termination_started = False
                    self._termination_done = None
                self._cancel_requested = False

    @staticmethod
    def _start_process(
        command: Sequence[str],
        cwd: str | Path | None,
        environment: Mapping[str, str],
    ) -> _Process:
        if sys.platform.startswith("linux"):
            return _PosixSpawnProcess.start(
                command,
                cwd=cwd,
                environment=environment,
            )
        options: dict[str, Any] = {
            "cwd": str(cwd) if cwd is not None else None,
            "env": dict(environment),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": False,
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        return subprocess.Popen(list(command), **options)

    def cancel(self) -> bool:
        termination_target: tuple[_Process, int | None, threading.Event] | None
        with self._lock:
            if not self._starting and self._process is None:
                return False
            self._cancel_requested = True
            process = self._process
            if (
                process is not None
                and not self._termination_started
                and self._termination_done is not None
            ):
                self._termination_started = True
                termination_target = (
                    process,
                    self._process_group_id,
                    self._termination_done,
                )
            else:
                termination_target = None
        if termination_target is not None:
            self._request_process_stop(*termination_target)
        return True

    @staticmethod
    def _request_process_stop(
        process: _Process,
        process_group_id: int | None,
        termination_done: threading.Event,
    ) -> None:
        if os.name == "nt":
            # CTRL_BREAK cannot be relied on for GUI-launched process groups.
            # taskkill /T /F is the deterministic way to stop both the CLI and
            # a collector child before communicate() waits for inherited pipes.
            try:
                ProcessController._terminate_windows_tree(process)
            finally:
                termination_done.set()
            return
        if process_group_id is None:
            termination_done.set()
            return
        worker = threading.Thread(
            target=ProcessController._terminate_posix_group,
            args=(process, process_group_id, termination_done),
            name="rscheck-gui-force-kill",
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError:
            try:
                ProcessController._signal_process_group(
                    process_group_id, signal.SIGKILL
                )
            finally:
                termination_done.set()

    @staticmethod
    def _terminate_posix_group(
        process: _Process,
        process_group_id: int,
        termination_done: threading.Event,
    ) -> None:
        try:
            try:
                ProcessController._signal_process_group(
                    process_group_id, signal.SIGTERM
                )
            except OSError:
                if process.poll() is None:
                    process.terminate()

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if not ProcessController._process_group_exists(process_group_id):
                    return
                time.sleep(0.05)

            try:
                ProcessController._signal_process_group(
                    process_group_id, signal.SIGKILL
                )
            except OSError:
                if process.poll() is None:
                    process.kill()

            # Give init a short opportunity to reap orphaned descendants so a
            # completed cancellation does not leave a visible process group.
            reap_deadline = time.monotonic() + 1
            while (
                time.monotonic() < reap_deadline
                and ProcessController._process_group_exists(process_group_id)
            ):
                time.sleep(0.02)
        finally:
            termination_done.set()

    @staticmethod
    def _signal_process_group(process_group_id: int, group_signal: int) -> None:
        os.killpg(process_group_id, group_signal)

    @staticmethod
    def _process_group_exists(process_group_id: int) -> bool:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _terminate_windows_tree(process: subprocess.Popen[str]) -> None:
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            if completed.returncode != 0 and process.poll() is None:
                process.kill()
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
