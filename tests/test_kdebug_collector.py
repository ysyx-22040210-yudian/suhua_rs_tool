from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import call, patch

from rscheck.inventory import load_inventory
from rscheck.kdebug_collector import (
    ACTION,
    API_VERSION,
    CollectorFailure,
    KDEBUG_ACTION_ERROR,
    KDEBUG_EXEC_ERROR,
    POSIX_SIGKILL,
    _invoke_kdebug,
    _resolve_kdebug,
    main,
)


class KdebugCollectorTests(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path, Path]:
        positions = root / "position list.txt"
        positions.write_text("top.u tile\ntop.u tile\n", encoding="utf-8")
        rules = root / "trace rules.tsv"
        rules.write_text("rs_pipe\tpipe_clock\n", encoding="utf-8")
        elab_db = root / "design database.elab++"
        elab_db.mkdir()
        output = root / "result inventory.json"
        return positions, rules, elab_db, output

    def _args(self, root: Path) -> tuple[list[str], Path]:
        positions, rules, elab_db, output = self._paths(root)
        return (
            [
                "--positions",
                str(positions),
                "--output",
                str(output),
                "--trace-rules",
                str(rules),
                "--trace-max-depth",
                "23",
                "--clk-port",
                "clk",
                "--rst-port",
                "rst_n",
                "--elab-db",
                str(elab_db),
            ],
            output,
        )

    @staticmethod
    def _inventory(position: str = "top.u tile") -> dict[str, object]:
        return {
            "schema_version": 3,
            "positions": {position: {"found": False, "instances": []}},
            "warnings": [],
            "notices": [],
        }

    @staticmethod
    def _response(**values: object) -> dict[str, object]:
        return {"api_version": API_VERSION, "action": ACTION, **values}

    def test_resolve_kdebug_accepts_compiled_elf_executable(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            executable = Path(name) / "kdebug"
            executable.write_bytes(b"\x7fELF" + b"\0" * 64)
            executable.chmod(0o755)

            with patch.dict(
                "os.environ", {"KDEBUG_BIN": str(executable.resolve())}, clear=False
            ):
                resolved = _resolve_kdebug()

            self.assertEqual(resolved, str(executable.resolve()))

    def test_resolve_kdebug_rejects_cpp_source_even_if_executable(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "kdebug.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            source.chmod(0o755)

            with patch.dict(
                "os.environ", {"KDEBUG_BIN": str(source.resolve())}, clear=False
            ), self.assertRaises(CollectorFailure) as raised:
                _resolve_kdebug()

            self.assertEqual(raised.exception.code, "KDEBUG_EXEC")
            self.assertIn("compiled executable", raised.exception.message)
            self.assertIn("not a C/C++ source file", raised.exception.message)

    def test_resolve_kdebug_rejects_executable_script_or_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            script = Path(name) / "kdebug"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)

            with patch.dict(
                "os.environ", {"KDEBUG_BIN": str(script.resolve())}, clear=False
            ), self.assertRaises(CollectorFailure) as raised:
                _resolve_kdebug()

            self.assertEqual(raised.exception.code, "KDEBUG_EXEC")
            self.assertIn("compiled Linux ELF executable", raised.exception.message)

    def test_resolve_kdebug_rejects_relative_kdebug_bin(self) -> None:
        with patch.dict("os.environ", {"KDEBUG_BIN": "kdebug"}, clear=False), self.assertRaises(
            CollectorFailure
        ) as raised:
            _resolve_kdebug()

        self.assertEqual(raised.exception.code, "KDEBUG_EXEC")
        self.assertIn("absolute path", raised.exception.message)

    def test_success_uses_one_json_action_and_writes_v3_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kdebug contract with spaces ") as name:
            root = Path(name)
            args, output = self._args(root)
            kdebug = root / "bin with spaces" / "kdebug"
            observed: dict[str, object] = {}

            def invoke(command, payload, hard_timeout_seconds, child_environment):
                observed["command"] = command
                observed["request"] = json.loads(payload.decode("utf-8"))
                observed["hard_timeout_seconds"] = hard_timeout_seconds
                observed["child_environment"] = child_environment
                response = self._response(
                    ok=True, data={"inventory": self._inventory()}
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(response).encode("utf-8"),
                    stderr=b"",
                )

            with patch.dict(
                "os.environ", {"RSCHECK_COLLECTOR_TIMEOUT_SECONDS": "30"}, clear=False
            ), patch(
                "rscheck.kdebug_collector._resolve_kdebug", return_value=str(kdebug)
            ), patch(
                "rscheck.kdebug_collector._invoke_kdebug", side_effect=invoke
            ):
                code = main(args)

            self.assertEqual(code, 0)
            self.assertEqual(observed["command"], [str(kdebug), "--json", "-"])
            self.assertEqual(observed["hard_timeout_seconds"], 27.0)
            child_environment = observed["child_environment"]
            self.assertIn("TMPDIR", child_environment)
            self.assertFalse(Path(child_environment["TMPDIR"]).exists())
            request = observed["request"]
            self.assertEqual(request["api_version"], API_VERSION)
            self.assertEqual(request["action"], ACTION)
            self.assertEqual(
                request["target"],
                {"elab_db": str((root / "design database.elab++").resolve())},
            )
            self.assertEqual(
                request["args"],
                {
                    "positions": ["top.u tile"],
                    "trace_rules": {"rs_pipe": "pipe_clock"},
                    "trace_max_depth": 23,
                    "clk_port": "clk",
                    "rst_port": "rst_n",
                },
            )
            self.assertEqual(request["output"], {"format": "json"})
            self.assertEqual(request["limits"], {"timeout_ms": 25000})
            self.assertEqual(load_inventory(output).schema_version, 3)

    def test_invalid_forwarded_timeout_fails_before_kdebug(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            args, output = self._args(root)
            diagnostic = StringIO()
            with patch.dict(
                "os.environ", {"RSCHECK_COLLECTOR_TIMEOUT_SECONDS": "2.5"}, clear=False
            ), patch(
                "rscheck.kdebug_collector._invoke_kdebug"
            ) as invoke, redirect_stderr(diagnostic):
                code = main(args)

            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
            invoke.assert_not_called()
            self.assertIn("error[TIMEOUT]", diagnostic.getvalue())

    def test_isolated_kdebug_home_is_created_and_passed_only_to_child(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            args, output = self._args(root)
            isolated_home = root / "isolated kdebug home"
            observed: dict[str, object] = {}

            def invoke(command, payload, hard_timeout_seconds, child_environment):
                observed["environment"] = child_environment
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        self._response(
                            ok=True, data={"inventory": self._inventory()}
                        )
                    ).encode("utf-8"),
                    stderr=b"",
                )

            with patch.dict(
                "os.environ",
                {"RSCHECK_KDEBUG_HOME": str(isolated_home)},
                clear=False,
            ), patch(
                "rscheck.kdebug_collector._resolve_kdebug",
                return_value="/mock/kdebug",
            ), patch(
                "rscheck.kdebug_collector._invoke_kdebug", side_effect=invoke
            ):
                code = main(args)

            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            self.assertTrue(isolated_home.is_dir())
            environment = observed["environment"]
            self.assertEqual(
                environment["KDEBUG_HOME"], str(isolated_home.resolve())
            )
            self.assertFalse(Path(environment["TMPDIR"]).exists())
            self.assertEqual(environment.get("HOME"), os.environ.get("HOME"))

    def test_relative_isolated_kdebug_home_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            args, output = self._args(root)
            diagnostic = StringIO()
            with patch.dict(
                "os.environ", {"RSCHECK_KDEBUG_HOME": "relative/home"}, clear=False
            ), patch(
                "rscheck.kdebug_collector._invoke_kdebug"
            ) as invoke, redirect_stderr(diagnostic):
                code = main(args)

            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
            invoke.assert_not_called()
            self.assertIn("error[KDEBUG_HOME]", diagnostic.getvalue())

    def test_action_failure_is_reported_and_does_not_write_output(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            args, output = self._args(root)
            response = self._response(
                ok=False,
                error={
                    "code": "KDB_LOAD_FAILED",
                    "message": "no queryable top",
                },
            )
            completed = subprocess.CompletedProcess(
                [],
                1,
                stdout=json.dumps(response).encode("utf-8"),
                stderr=b"kdebug load diagnostic\n",
            )
            diagnostic = StringIO()
            with patch(
                "rscheck.kdebug_collector._resolve_kdebug", return_value="/mock/kdebug"
            ), patch(
                "rscheck.kdebug_collector._invoke_kdebug", return_value=completed
            ), redirect_stderr(diagnostic):
                code = main(args)

            self.assertEqual(code, KDEBUG_ACTION_ERROR)
            self.assertFalse(output.exists())
            self.assertIn("kdebug load diagnostic", diagnostic.getvalue())
            self.assertIn("error[KDEBUG_ACTION]", diagnostic.getvalue())
            self.assertIn("KDB_LOAD_FAILED: no queryable top", diagnostic.getvalue())

    def test_invalid_v3_response_does_not_replace_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            args, output = self._args(root)
            output.write_text("existing output\n", encoding="utf-8")
            invalid = self._inventory()
            invalid["schema_version"] = 2
            response = self._response(ok=True, data={"inventory": invalid})
            completed = subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(response).encode("utf-8"),
                stderr=b"",
            )
            diagnostic = StringIO()
            with patch(
                "rscheck.kdebug_collector._resolve_kdebug", return_value="/mock/kdebug"
            ), patch(
                "rscheck.kdebug_collector._invoke_kdebug", return_value=completed
            ), redirect_stderr(diagnostic):
                code = main(args)

            self.assertEqual(code, KDEBUG_ACTION_ERROR)
            self.assertEqual(output.read_text("utf-8"), "existing output\n")
            self.assertIn("error[KDEBUG_RESPONSE]", diagnostic.getvalue())
            self.assertIn("expected 3", diagnostic.getvalue())

    def test_partial_load_notice_is_emitted_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            args, output = self._args(root)
            inventory = self._inventory()
            inventory["notices"] = [
                "NPI_LOAD_PARTIAL: top remains queryable after elaboration errors"
            ]
            response = self._response(ok=True, data={"inventory": inventory})
            completed = subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(response).encode("utf-8"),
                stderr=b"Verdi load diagnostic\n",
            )
            diagnostic = StringIO()
            with patch(
                "rscheck.kdebug_collector._resolve_kdebug", return_value="/mock/kdebug"
            ), patch(
                "rscheck.kdebug_collector._invoke_kdebug", return_value=completed
            ), redirect_stderr(diagnostic):
                code = main(args)

            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            self.assertIn("Verdi load diagnostic", diagnostic.getvalue())
            self.assertIn(
                "warning[NPI_LOAD_PARTIAL]: NPI_LOAD_PARTIAL: top remains queryable",
                diagnostic.getvalue(),
            )

    def test_response_version_and_action_must_match(self) -> None:
        for field, wrong_value in (
            ("api_version", "kdebug.v2"),
            ("action", "trace.driver"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                args, output = self._args(root)
                output.write_text("existing output\n", encoding="utf-8")
                response = self._response(
                    ok=True, data={"inventory": self._inventory()}
                )
                response[field] = wrong_value
                completed = subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=json.dumps(response).encode("utf-8"),
                    stderr=b"",
                )
                diagnostic = StringIO()
                with patch(
                    "rscheck.kdebug_collector._resolve_kdebug",
                    return_value="/mock/kdebug",
                ), patch(
                    "rscheck.kdebug_collector._invoke_kdebug",
                    return_value=completed,
                ), redirect_stderr(diagnostic):
                    code = main(args)

                self.assertEqual(code, KDEBUG_ACTION_ERROR)
                self.assertEqual(output.read_text("utf-8"), "existing output\n")
                self.assertIn("error[KDEBUG_RESPONSE]", diagnostic.getvalue())
                self.assertIn(field, diagnostic.getvalue())

    def test_hard_timeout_terminates_kdebug_process_group(self) -> None:
        class TimedOutProcess:
            pid = 4242
            returncode = None

            def __init__(self) -> None:
                self.communicate_calls = 0

            def poll(self):
                return self.returncode

            def communicate(self, input=None, timeout=None):
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    raise subprocess.TimeoutExpired(["kdebug"], timeout)
                return b"", b"timeout diagnostic"

            def wait(self, timeout=None):
                self.returncode = -signal.SIGTERM
                return self.returncode

        process = TimedOutProcess()
        with patch("rscheck.kdebug_collector.os.name", "posix"), patch(
            "rscheck.kdebug_collector.subprocess.Popen", return_value=process
        ) as popen, patch(
            "rscheck.kdebug_collector.os.killpg", create=True
        ) as kill_group:
            with self.assertRaises(CollectorFailure) as raised:
                _invoke_kdebug(["/mock/kdebug", "--json", "-"], b"{}\n", 0.25)

        self.assertEqual(raised.exception.code, "KDEBUG_TIMEOUT")
        popen.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(
            kill_group.call_args_list,
            [call(4242, signal.SIGTERM), call(4242, POSIX_SIGKILL)],
        )
        self.assertEqual(process.communicate_calls, 2)

    def test_termination_sweeps_group_after_frontend_already_exited(self) -> None:
        class ExitedProcess:
            pid = 4343
            returncode = -signal.SIGTERM

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                raise AssertionError("an exited leader must not be waited again")

        with patch("rscheck.kdebug_collector.os.name", "posix"), patch(
            "rscheck.kdebug_collector.os.killpg", create=True
        ) as kill_group, patch("rscheck.kdebug_collector.time.sleep"):
            from rscheck.kdebug_collector import _terminate_kdebug

            _terminate_kdebug(ExitedProcess())

        self.assertEqual(
            kill_group.call_args_list,
            [call(4343, signal.SIGTERM), call(4343, POSIX_SIGKILL)],
        )

    def test_runtime_tmpdir_is_removed_after_kdebug_failure(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            args, output = self._args(root)
            observed: dict[str, Path] = {}

            def invoke(command, payload, hard_timeout_seconds, child_environment):
                runtime = Path(child_environment["TMPDIR"])
                observed["runtime"] = runtime
                leftover = runtime / "kdebug-tcl-npi-leftover"
                leftover.mkdir()
                (leftover / "diagnostic").write_text("retained", encoding="utf-8")
                raise CollectorFailure(
                    KDEBUG_EXEC_ERROR, "KDEBUG_TIMEOUT", "simulated timeout"
                )

            diagnostic = StringIO()
            with patch(
                "rscheck.kdebug_collector._resolve_kdebug",
                return_value="/mock/kdebug",
            ), patch(
                "rscheck.kdebug_collector._invoke_kdebug", side_effect=invoke
            ), redirect_stderr(diagnostic):
                code = main(args)

            self.assertEqual(code, KDEBUG_EXEC_ERROR)
            self.assertFalse(output.exists())
            self.assertFalse(observed["runtime"].exists())
            self.assertIn("error[KDEBUG_TIMEOUT]", diagnostic.getvalue())

    def test_adapter_source_has_no_direct_npi_dependency(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sources = (
            (root / "rscheck" / "kdebug_collector.py").read_text("utf-8"),
            (root / "scripts" / "rs_kdebug_collector.py").read_text("utf-8"),
        )
        for source in sources:
            self.assertNotIn("libNPI", source)
            self.assertNotIn("libnpiL1", source)
            self.assertNotIn("ctypes", source)
            self.assertNotIn("cffi", source)


if __name__ == "__main__":
    unittest.main()
