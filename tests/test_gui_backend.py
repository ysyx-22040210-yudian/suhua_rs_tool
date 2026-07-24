from __future__ import annotations

import json
import os
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
            validate_headers=True,
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
        self.assertIn("--header-check", command)

    def test_header_check_can_be_explicitly_disabled(self) -> None:
        command = build_validate_command(self._request(validate_headers=False))
        self.assertIn("--no-header-check", command)
        self.assertNotIn("--header-check", command)

    def test_duplicate_and_invalid_columns_are_rejected(self) -> None:
        duplicate = default_columns()
        duplicate["rst"] = duplicate["clk"]
        with self.assertRaisesRegex(GuiInputError, "unique"):
            build_validate_command(self._request(columns=duplicate))

        invalid = default_columns()
        invalid["step"] = "0"
        with self.assertRaisesRegex(GuiInputError, "positive integer"):
            build_validate_command(self._request(columns=invalid))

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
        }
        self.assertEqual(load_validation_rows(json.dumps([row]))[0]["row"], 2)
        del row["position"]
        with self.assertRaisesRegex(GuiReportError, "missing"):
            load_validation_rows(json.dumps([row]))

    def test_report_schema_and_summary_are_checked(self) -> None:
        spec = {
            "row": 2,
            **{name: f"value-{index}" for index, name in enumerate(FIELD_NAMES)},
        }
        report = {
            "schema_version": 1,
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
                    "matched_instances": [],
                    "findings": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertTrue(load_report(path).summary["passed"])

            report["summary"]["rows"] = 2
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "rows length"):
                load_report(path)

            report["summary"]["rows"] = 1
            report["summary"]["warnings"] = 1
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "finding counts"):
                load_report(path)

            report["schema_version"] = 2
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GuiReportError, "schema_version"):
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
