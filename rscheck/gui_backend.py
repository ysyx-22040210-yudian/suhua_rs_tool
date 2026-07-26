from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .checker import parameter_value_state
from .model import FIELD_NAMES


LIVE_SOURCE = "live"
INVENTORY_SOURCE = "inventory"


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
    if type(schema_version) is not int or schema_version not in {2, 3}:
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
        if schema_version == 3:
            _validate_v3_row(row, spec, matched_instances, index + 1)
        row_findings = _mapping_array(
            row.get("findings"), f"report row {index + 1}.findings"
        )
        _validate_finding_severities(
            row_findings, f"report row {index + 1}.findings"
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


class ProcessController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
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
        options: dict[str, Any] = {
            "cwd": str(cwd) if cwd is not None else None,
            "env": child_environment,
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

        try:
            process = subprocess.Popen(list(command), **options)
        except Exception:
            with self._lock:
                self._starting = False
                self._cancel_requested = False
                self._termination_done = None
            raise
        termination_target: tuple[subprocess.Popen[str], int | None, threading.Event] | None
        with self._lock:
            self._process = process
            # start_new_session=True guarantees that the child's PID is the
            # stable process-group ID even if the group leader exits first.
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

    def cancel(self) -> bool:
        termination_target: tuple[subprocess.Popen[str], int | None, threading.Event] | None
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
        process: subprocess.Popen[str],
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
        process: subprocess.Popen[str],
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
