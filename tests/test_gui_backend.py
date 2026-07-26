from __future__ import annotations

import copy
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rscheck.cli import main as cli_main
from rscheck.gui_backend import (
    INVENTORY_SOURCE,
    LIVE_SOURCE,
    GuiInputError,
    GuiReportError,
    GuiRunRequest,
    ProcessController,
    build_check_command,
    build_validate_command,
    default_columns,
    load_report,
    load_validation_rows,
)
from rscheck.model import FIELD_NAMES
from scripts.test_rscheck_gui_smoke import _online_command_contract_errors


ROOT = Path(__file__).resolve().parents[1]


class GuiBackendTests(unittest.TestCase):
    def _request(self, **overrides: object) -> GuiRunRequest:
        request = GuiRunRequest(
            excel_path=str(ROOT / "tests" / "fixtures" / "specs.csv"),
            config_path=str(ROOT / "config" / "rscheck.example.json"),
            columns=default_columns(),
            sheet="1",
            header_row="1",
            data_start_row="2",
            validate_headers=False,
            source_mode=LIVE_SOURCE,
            collector_path=str(ROOT / "npi" / "build" / "rs_npi_collector"),
            elab_db_path=str(ROOT / "output" / "design with spaces" / "kdb.elab++"),
            npi_lib_dir=str(ROOT / "tools" / "NPI lib"),
            npi_timeout="180",
            keep_inventory_path=str(ROOT / "output" / "inventory.json"),
            json_report_path=str(ROOT / "output" / "report.json"),
            csv_report_path=str(ROOT / "output" / "report.csv"),
        )
        return replace(request, **overrides)

    def test_validate_command_contains_all_column_overrides(self) -> None:
        columns = {
            "Intf_type": "13",
            "RS_module": "2",
            "RS_inst": "21",
            "position": "8",
            "step": "5",
            "clk": "34",
            "rst": "3",
            "CRG_source": "55",
            "RS_CFG_EN": "89",
        }
        command = build_validate_command(
            self._request(columns=columns), python_executable="python-under-test"
        )
        self.assertEqual(command[:4], ["python-under-test", "-m", "rscheck", "validate"])
        self.assertEqual(command[-1], "--json")
        mappings = [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--column"
        ]
        self.assertEqual(mappings, [f"{name}={columns[name]}" for name in FIELD_NAMES])
        self.assertIn("--no-header-check", command)
        self.assertNotIn("--header-check", command)

    def test_header_check_can_be_explicitly_enabled(self) -> None:
        command = build_validate_command(self._request(validate_headers=True))
        self.assertIn("--header-check", command)
        self.assertNotIn("--no-header-check", command)

    def test_gui_request_defaults_to_column_mapping_without_header_check(self) -> None:
        request = GuiRunRequest(excel_path="spec.xlsx", config_path="config.json")
        self.assertFalse(request.validate_headers)

    def test_default_columns_include_rs_cfg_en_as_ninth_mapping(self) -> None:
        columns = default_columns()
        self.assertEqual(len(columns), 9)
        self.assertEqual(columns["RS_CFG_EN"], "9")

    def test_duplicate_and_invalid_columns_are_rejected(self) -> None:
        duplicate = default_columns()
        duplicate["rst"] = duplicate["clk"]
        with self.assertRaisesRegex(GuiInputError, "unique"):
            build_validate_command(self._request(columns=duplicate))

        invalid = default_columns()
        invalid["step"] = "0"
        with self.assertRaisesRegex(GuiInputError, "positive integer"):
            build_validate_command(self._request(columns=invalid))

        invalid["step"] = "²"
        with self.assertRaisesRegex(GuiInputError, "positive integer"):
            build_validate_command(self._request(columns=invalid))

    def test_sheet_names_preserve_non_ascii_digits_and_surrounding_spaces(self) -> None:
        for sheet_name in ("²", " 123 "):
            with self.subTest(sheet_name=sheet_name):
                command = build_validate_command(self._request(sheet=sheet_name))
                self.assertEqual(command[command.index("--sheet") + 1], sheet_name)

        with self.assertRaisesRegex(GuiInputError, "must not be blank"):
            build_validate_command(self._request(sheet="   "))

    def test_invalid_row_relationship_is_rejected(self) -> None:
        with self.assertRaisesRegex(GuiInputError, "after the header"):
            build_validate_command(self._request(header_row="4", data_start_row="4"))

    def test_live_command_accepts_only_collector_and_elaborated_kdb(self) -> None:
        command = build_check_command(
            self._request(), python_executable="python-under-test"
        )
        self.assertEqual(command[:4], ["python-under-test", "-m", "rscheck", "check"])
        self.assertEqual(command.count("--collector"), 1)
        self.assertEqual(command.count("--elab-db"), 1)
        self.assertIn(str(ROOT / "output" / "design with spaces" / "kdb.elab++"), command)
        self.assertNotIn("--inventory", command)
        for forbidden in ("-f", "-sv", "-lib", "-top", "--"):
            self.assertNotIn(forbidden, command)

        collector = command[command.index("--collector") + 1]
        elab_db = command[command.index("--elab-db") + 1]
        contract_arguments = {
            "expected_collector": collector,
            "expected_elab_db": elab_db,
        }
        self.assertEqual(
            _online_command_contract_errors(
                shlex.join(command),
                expected_command=command,
                **contract_arguments,
            ),
            (),
        )

        wrong_kdb = list(command)
        wrong_kdb[wrong_kdb.index("--elab-db") + 1] = "/other/kdb.elab++"
        self.assertTrue(
            _online_command_contract_errors(
                shlex.join(wrong_kdb),
                expected_command=wrong_kdb,
                **contract_arguments,
            )
        )

        forbidden_design_inputs = (
            ("-F", "rtl.f"),
            ("-verilog", "top.sv"),
            ("-vhdl", "top.vhd"),
            ("-path", "rtl"),
            ("--filelist", "rtl.f"),
            ("--top", "top"),
            ("top.sv",),
        )
        for extra_arguments in forbidden_design_inputs:
            with self.subTest(extra_arguments=extra_arguments):
                mutated = [*command, *extra_arguments]
                self.assertTrue(
                    _online_command_contract_errors(
                        shlex.join(mutated),
                        expected_command=mutated,
                        **contract_arguments,
                    )
                )

    def test_work_library_is_rejected_as_live_design(self) -> None:
        with self.assertRaisesRegex(GuiInputError, "work.lib\+\+"):
            build_check_command(
                self._request(elab_db_path=str(ROOT / "output" / "work.lib++") + "//")
            )

    def test_inventory_mode_is_mutually_exclusive_in_command(self) -> None:
        command = build_check_command(
            self._request(
                source_mode=INVENTORY_SOURCE,
                inventory_path=str(ROOT / "tests" / "fixtures" / "inventory.json"),
            )
        )
        self.assertIn("--inventory", command)
        for option in ("--collector", "--elab-db", "--npi-lib-dir", "--keep-inventory"):
            self.assertNotIn(option, command)

    def test_unknown_source_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(GuiInputError, "unsupported"):
            build_check_command(self._request(source_mode="filelist"))

    def test_outputs_cannot_alias_inputs_or_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            excel = root / "规格.xlsx"
            config = root / "配置.json"
            collector = root / "collector"
            kdb = root / "kdb.elab++"
            kdb.mkdir()
            npi_lib = root / "NPI lib"
            npi_lib.mkdir()
            for path in (excel, config, collector):
                path.touch()

            request = self._request(
                excel_path=str(excel),
                config_path=str(config),
                collector_path=str(collector),
                elab_db_path=str(kdb),
                npi_lib_dir=str(npi_lib),
                json_report_path=str(excel),
                csv_report_path=str(root / "report.csv"),
                keep_inventory_path=str(root / "inventory.json"),
            )
            with self.assertRaisesRegex(GuiInputError, "must not overwrite Excel"):
                build_check_command(request)

            same = root / "same report"
            with self.assertRaisesRegex(GuiInputError, "same path"):
                build_check_command(
                    replace(
                        request,
                        json_report_path=str(same),
                        csv_report_path=str(same),
                        keep_inventory_path="",
                    )
                )

            with self.assertRaisesRegex(GuiInputError, "inside the elaborated KDB"):
                build_check_command(
                    replace(
                        request,
                        json_report_path=str(kdb / "report.json"),
                        csv_report_path="",
                        keep_inventory_path="",
                    )
                )

            with self.assertRaisesRegex(GuiInputError, "inside the NPI library"):
                build_check_command(
                    replace(
                        request,
                        json_report_path=str(npi_lib / "libNPI.so"),
                        csv_report_path="",
                        keep_inventory_path="",
                    )
                )

    def test_relative_report_path_is_resolved_to_project_root(self) -> None:
        command = build_check_command(
            self._request(json_report_path="output/结果 report.json", csv_report_path="")
        )
        report_path = Path(command[command.index("--json-report") + 1])
        self.assertTrue(report_path.is_absolute())
        self.assertEqual(report_path.name, "结果 report.json")

    def test_validation_rows_are_structurally_checked(self) -> None:
        row = {
            "row": 2,
            **{name: f"value-{index}" for index, name in enumerate(FIELD_NAMES)},
            "position_alias": "core_pipe",
        }
        loaded = load_validation_rows(json.dumps([row]))[0]
        self.assertEqual(loaded["row"], 2)
        self.assertEqual(loaded["position_alias"], "core_pipe")
        self.assertEqual(loaded["position"], "value-3")
        row["position_alias"] = None
        with self.assertRaisesRegex(GuiReportError, "position_alias must be a string"):
            load_validation_rows(json.dumps([row]))
        row["position_alias"] = "core_pipe"
        del row["position"]
        with self.assertRaisesRegex(GuiReportError, "missing"):
            load_validation_rows(json.dumps([row]))

    def test_report_schema_and_summary_are_checked(self) -> None:
        spec = {
            "row": 2,
            **{name: f"value-{index}" for index, name in enumerate(FIELD_NAMES)},
            "position_alias": "core_pipe",
        }
        report = {
            "schema_version": 2,
            "summary": {
                "passed": True,
                "rows": 1,
                "passed_rows": 1,
                "failed_rows": 0,
                "errors": 0,
                "warnings": 0,
            },
            "global_findings": [],
            "rows": [
                {
                    "spec": spec,
                    "passed": True,
                    "matched_instances": [
                        {
                            "name": "PIPE_S0",
                            "full_name": "top.u.PIPE_S0",
                            "module": "pipe",
                            "file": "pipe.sv",
                            "line": 1,
                            "parameters": {"RS_CFG_EN": "0", "UNRESOLVED": None},
                            "ports": {},
                            "clk_sources": [],
                        }
                    ],
                    "findings": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            loaded = load_report(path)
            self.assertTrue(loaded.summary["passed"])
            self.assertEqual(
                loaded.rows[0]["matched_instances"][0]["parameters"]["RS_CFG_EN"],
                "0",
            )
            self.assertEqual(loaded.rows[0]["spec"]["position_alias"], "core_pipe")
            self.assertEqual(loaded.rows[0]["spec"]["position"], "value-3")

            spec["position_alias"] = None
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(
                GuiReportError, "position_alias must be a string"
            ):
                load_report(path)
            spec["position_alias"] = "core_pipe"

            parameters = report["rows"][0]["matched_instances"][0].pop("parameters")
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "parameters"):
                load_report(path)
            report["rows"][0]["matched_instances"][0]["parameters"] = parameters

            parameters["RS_CFG_EN"] = 0
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "string or null"):
                load_report(path)
            parameters["RS_CFG_EN"] = "0"

            parameters[""] = "0"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "names must not be empty"):
                load_report(path)
            del parameters[""]

            spec["RS_CFG_EN"] = None
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "RS_CFG_EN must be a string"):
                load_report(path)
            spec["RS_CFG_EN"] = "value-8"

            report["rows"][0]["passed"] = False
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "passed does not match"):
                load_report(path)
            report["rows"][0]["passed"] = True

            report["rows"][0]["findings"] = [
                {"severity": "ERROR", "code": "BROKEN_SEVERITY"}
            ]
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "severity"):
                load_report(path)
            report["rows"][0]["findings"] = []

            report["summary"]["rows"] = 2
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "rows length"):
                load_report(path)

            report["summary"]["rows"] = 1
            report["summary"]["warnings"] = 1
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "finding counts"):
                load_report(path)
            report["summary"]["warnings"] = 0

            for unsupported_version in (1, 2.0, True, "2", 4):
                with self.subTest(schema_version=unsupported_version):
                    report["schema_version"] = unsupported_version
                    path.write_text(json.dumps(report), encoding="utf-8")
                    with self.assertRaisesRegex(GuiReportError, "schema_version"):
                        load_report(path)

    def test_report_schema_v3_step_evidence_is_checked(self) -> None:
        spec = {
            "row": 2,
            **{name: f"value-{index}" for index, name in enumerate(FIELD_NAMES)},
        }
        spec["step"] = 1
        evaluation = {
            "instance": "top.u.PIPE_S0",
            "contribution": 1,
            "parameters": {
                "rs_mode": {
                    "present": True,
                    "raw_value": "1",
                    "state": "nonzero",
                }
            },
        }
        report = {
            "schema_version": 3,
            "summary": {
                "passed": True,
                "rows": 1,
                "passed_rows": 1,
                "failed_rows": 0,
                "errors": 0,
                "warnings": 0,
            },
            "global_findings": [],
            "rows": [
                {
                    "spec": spec,
                    "passed": True,
                    "module_rule": {
                        "name": spec["RS_module"],
                        "has_rs_cfg_en": True,
                        "step_parameters": ["rs_mode"],
                    },
                    "step_check": {
                        "expected": 1,
                        "physical_instances": 1,
                        "effective_step": 1,
                        "contributions": [evaluation],
                    },
                    "matched_instances": [
                        {
                            "name": "PIPE_S0",
                            "full_name": "top.u.PIPE_S0",
                            "module": spec["RS_module"],
                            "file": "pipe.sv",
                            "line": 1,
                            "parameters": {"RS_CFG_EN": "0", "rs_mode": "1"},
                            "ports": {},
                            "clk_sources": [],
                            "step_evaluation": evaluation,
                        }
                    ],
                    "findings": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "report-v3.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            loaded = load_report(path)
            self.assertEqual(loaded.rows[0]["step_check"]["effective_step"], 1)

            report["rows"][0]["step_check"]["effective_step"] = 0
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "effective_step"):
                load_report(path)

            def assert_rejected(mutator, message: str) -> None:
                candidate = copy.deepcopy(report)
                candidate["rows"][0]["step_check"]["effective_step"] = 1
                mutator(candidate)
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaisesRegex(GuiReportError, message):
                    load_report(path)

            assert_rejected(
                lambda candidate: candidate["rows"][0]["step_check"].__setitem__(
                    "expected", True
                ),
                "expected",
            )
            assert_rejected(
                lambda candidate: candidate["rows"][0]["step_check"].__setitem__(
                    "effective_step", True
                ),
                "integer or null",
            )
            assert_rejected(
                lambda candidate: candidate["rows"][0]["module_rule"].__setitem__(
                    "step_parameters", ["other_mode"]
                ),
                "module_rule.step_parameters",
            )
            assert_rejected(
                lambda candidate: candidate["rows"][0]["step_check"][
                    "contributions"
                ][0]["parameters"]["rs_mode"].__setitem__("raw_value", "0"),
                "does not match instance parameters",
            )
            assert_rejected(
                lambda candidate: candidate["rows"][0]["step_check"][
                    "contributions"
                ][0]["parameters"]["rs_mode"].__setitem__("state", "zero"),
                "state does not match",
            )

            def make_consistent_zero_but_pass(candidate) -> None:
                row = candidate["rows"][0]
                row["matched_instances"][0]["parameters"]["rs_mode"] = "0"
                for evaluation in (
                    row["step_check"]["contributions"][0],
                    row["matched_instances"][0]["step_evaluation"],
                ):
                    evaluation["contribution"] = 0
                    evaluation["parameters"]["rs_mode"]["raw_value"] = "0"
                    evaluation["parameters"]["rs_mode"]["state"] = "zero"
                row["step_check"]["effective_step"] = 0

            assert_rejected(
                make_consistent_zero_but_pass,
                "passed is inconsistent with effective_step",
            )

    def test_report_schema_v3_missing_group_keeps_effective_step_null(self) -> None:
        spec = {
            "row": 2,
            **{name: f"value-{index}" for index, name in enumerate(FIELD_NAMES)},
        }
        spec["step"] = 0
        report = {
            "schema_version": 3,
            "summary": {
                "passed": False,
                "rows": 1,
                "passed_rows": 0,
                "failed_rows": 1,
                "errors": 1,
                "warnings": 0,
            },
            "global_findings": [],
            "rows": [
                {
                    "spec": spec,
                    "passed": False,
                    "module_rule": {
                        "name": spec["RS_module"],
                        "has_rs_cfg_en": False,
                        "step_parameters": [],
                    },
                    "step_check": {
                        "expected": 0,
                        "physical_instances": 0,
                        "effective_step": None,
                        "contributions": [],
                    },
                    "matched_instances": [],
                    "findings": [
                        {
                            "severity": "error",
                            "code": "GROUP_NOT_FOUND",
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "report-v3-missing-group.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            loaded = load_report(path)
            self.assertIsNone(loaded.rows[0]["step_check"]["effective_step"])

            report["rows"][0]["step_check"]["effective_step"] = 0
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "effective_step"):
                load_report(path)

    def test_cli_gui_subcommand_dispatches_without_loading_a_config(self) -> None:
        with patch("rscheck.gui.main", return_value=0) as gui_main:
            self.assertEqual(cli_main(["gui"]), 0)
        gui_main.assert_called_once_with()


class ProcessControllerTests(unittest.TestCase):
    @staticmethod
    def _posix_pid_exists(process_id: int) -> bool:
        try:
            status = Path(f"/proc/{process_id}/status").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            pass
        else:
            for line in status.splitlines():
                if line.startswith("State:"):
                    fields = line.split()
                    if len(fields) > 1 and fields[1] == "Z":
                        return False
                    break
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def test_process_output_and_environment_are_captured(self) -> None:
        controller = ProcessController()
        result = controller.run(
            [
                sys.executable,
                "-c",
                "import os,sys; print(os.environ['GUI_TEST_VALUE']); print('err', file=sys.stderr)",
            ],
            environment={"GUI_TEST_VALUE": "captured"},
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "captured")
        self.assertEqual(result.stderr.strip(), "err")
        self.assertFalse(result.cancelled)
        self.assertFalse(controller.is_running)

    def test_running_process_can_be_cancelled(self) -> None:
        controller = ProcessController()
        outcomes = []

        def run() -> None:
            outcomes.append(
                controller.run([sys.executable, "-c", "import time; time.sleep(30)"])
            )

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 5
        while not controller.is_running and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(controller.is_running)
        self.assertTrue(controller.cancel())
        worker.join(timeout=8)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].cancelled)
        self.assertNotEqual(outcomes[0].returncode, 0)
        self.assertFalse(controller.is_running)

    def test_cancel_during_process_start_is_not_lost(self) -> None:
        controller = ProcessController()
        outcomes = []
        popen_entered = threading.Event()
        allow_popen = threading.Event()
        real_popen = subprocess.Popen
        first_call = True

        def delayed_popen(*args, **kwargs):
            nonlocal first_call
            if first_call:
                first_call = False
                popen_entered.set()
                if not allow_popen.wait(timeout=5):
                    raise RuntimeError("test did not release Popen")
            return real_popen(*args, **kwargs)

        def run() -> None:
            outcomes.append(
                controller.run([sys.executable, "-c", "import time; time.sleep(30)"])
            )

        with patch("rscheck.gui_backend.subprocess.Popen", side_effect=delayed_popen):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(popen_entered.wait(timeout=5))
            try:
                self.assertTrue(controller.is_running)
                self.assertTrue(controller.cancel())
            finally:
                allow_popen.set()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].cancelled)
        self.assertNotEqual(outcomes[0].returncode, 0)
        self.assertFalse(controller.is_running)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group semantics required")
    def test_cancel_kills_descendant_after_group_leader_exits(self) -> None:
        controller = ProcessController()
        outcomes = []
        child_pid = None
        with tempfile.TemporaryDirectory() as name:
            pid_path = Path(name) / "child.pid"
            child_code = (
                "import pathlib,os,signal,time; "
                "signal.signal(signal.SIGTERM, lambda *_: None); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding='ascii'); "
                "time.sleep(60)"
            )
            parent_code = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                "time.sleep(60)"
            )

            def run() -> None:
                outcomes.append(controller.run([sys.executable, "-c", parent_code]))

            worker = threading.Thread(target=run)
            worker.start()
            try:
                deadline = time.monotonic() + 5
                while child_pid is None and time.monotonic() < deadline:
                    try:
                        raw_pid = pid_path.read_text(encoding="ascii").strip()
                        if raw_pid:
                            child_pid = int(raw_pid)
                    except (FileNotFoundError, ValueError):
                        pass
                    if child_pid is None:
                        time.sleep(0.01)
                self.assertIsNotNone(child_pid, "descendant did not start")
                assert child_pid is not None
                self.assertTrue(controller.cancel())
                worker.join(timeout=12)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(outcomes), 1)
                self.assertTrue(outcomes[0].cancelled)
                self.assertNotEqual(outcomes[0].returncode, 0)
                self.assertFalse(controller.is_running)

                deadline = time.monotonic() + 3
                while (
                    self._posix_pid_exists(child_pid)
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                self.assertFalse(self._posix_pid_exists(child_pid))
            finally:
                if controller.is_running:
                    controller.cancel()
                worker.join(timeout=12)
                if child_pid is not None and self._posix_pid_exists(child_pid):
                    os.kill(child_pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
