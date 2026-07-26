from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rscheck.model import InventoryError, ModuleRule, RtlConfig, SpecRow
from rscheck.npi_runner import _collector_environment, collect_inventory


class NpiCollectorContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = Path(__file__).resolve().parents[1]

    def test_collector_has_bounded_module_hop_clock_tracing(self) -> None:
        source = (self.project_root / "npi" / "rs_npi_collector.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn('#include "npi_L1.h"', source)
        self.assertIn("npi_iterate(npiPort, module)", source)
        self.assertIn(
            "npi_mod_inst_get_port(&mutable_full_name[0], fallback_ports)", source
        )
        self.assertNotIn("name == clk_port_", source)
        self.assertIn('argument == "--trace-rules"', source)
        self.assertIn('argument == "--trace-max-depth"', source)
        self.assertIn("kDefaultTraceMaxDepth = 16", source)
        self.assertIn("kMaximumTraceMaxDepth = 256", source)
        self.assertIn("kMaxTraceObjectVisits = 100000", source)
        self.assertIn("npi_nl_iterate(npiNlDriver, object)", source)
        self.assertIn("npi_nl_iterate(npiNlConnectivity, object)", source)
        self.assertIn(
            "walk_netlist_drivers(connected, module_depth, stack_depth + 1",
            source,
        )
        self.assertIn("connected_type == npiNlConcatNet", source)
        self.assertIn("connected_type == npiNlSliceNet", source)
        self.assertIn("connected_type == npiNlPseudoNet", source)
        self.assertIn("NPI Netlist module port has unknown direction", source)
        self.assertIn("npiNlInstPort, instance, current", source)
        self.assertIn('name == "clk" || name == "rst_n"', source)
        self.assertIn("intermediate port(s) with unknown direction", source)
        self.assertIn("mark_trace_unresolved(state, message.str())", source)
        self.assertIn("depth >= state->trace.max_depth", source)
        self.assertIn('state.trace.status = "depth_limited"', source)
        self.assertIn('state.trace.status = "unresolved"', source)
        self.assertIn('\\"schema_version\\": 3', source)
        self.assertIn('\\"clock_trace\\": ', source)
        self.assertIn(
            '\\"excluded_inputs\\": [\\"clk\\", \\"rst_n\\"]', source
        )
        self.assertIn("result.clk_sources = result.clock_trace.modules", source)

    def test_clock_trace_cache_isolated_by_instance_hierarchy(self) -> None:
        source = (self.project_root / "npi" / "rs_npi_collector.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'instance_full_name + "\\x1f" + clock_port + "\\x1f"',
            source,
        )
        self.assertNotIn(
            'std::string cache_key = clock_port + "\\x1f";', source
        )

    def test_partial_kdb_always_merges_language_model_input_ports(self) -> None:
        source = (self.project_root / "npi" / "rs_npi_collector.cpp").read_text(
            encoding="utf-8"
        )
        start = source.index("  void expand_module_inputs(")
        end = source.index("  ClockTrace trace_clock(", start)
        body = source[start:end]
        fallback_call = "trace_language_input_ports(current, queue, state)"
        fallback_guard = "if (!fallback_available &&"
        self.assertIn("const bool fallback_available", body)
        self.assertIn(fallback_call, body)
        self.assertIn(fallback_guard, body)
        self.assertLess(body.index(fallback_call), body.index(fallback_guard))
        self.assertNotIn(
            "if (scanned_ports == 0 || unknown_directions != 0) {", body
        )

    def test_makefile_requires_and_links_verdi_npi_l1(self) -> None:
        makefile = (self.project_root / "npi" / "Makefile").read_text(
            encoding="utf-8"
        )
        self.assertIn("NPI_L1_INC ?= $(NPI_ROOT)/L1/C/inc", makefile)
        self.assertIn("NPI_PLATFORM_LOWER :=", makefile)
        self.assertIn("NPI_L1_LIB ?= $(NPI_DEFAULT_L1_LIB)", makefile)
        self.assertIn('$(NPI_L1_INC)/npi_L1.h', makefile)
        self.assertIn('$(NPI_L1_LIB)/libnpiL1.so', makefile)
        self.assertIn("-lnpiL1 -lNPI", makefile)


class NpiRunnerTests(unittest.TestCase):
    def _spec(self) -> SpecRow:
        return SpecRow(
            source=Path("specs.csv"),
            sheet="CSV",
            row_number=2,
            intf_type="OUT_IF",
            rs_module="rs_pipe",
            rs_inst="PIPE",
            position="top.u",
            step=1,
            clk="clk",
            rst="rst",
            crg_source="crg",
            rs_cfg_en="",
        )

    def test_verdi_home_npi_library_is_prepended(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            verdi_home = Path(name)
            library = verdi_home / "share" / "NPI" / "lib" / "LINUX64"
            library.mkdir(parents=True)
            (library / "libNPI.so").touch()
            with patch.dict(
                os.environ,
                {
                    "VERDI_HOME": str(verdi_home),
                    "NPI_PLATFORM": "LINUX64",
                    "LD_LIBRARY_PATH": "/existing/lib",
                },
                clear=True,
            ):
                environment = _collector_environment()
            entries = environment["LD_LIBRARY_PATH"].split(os.pathsep)
            self.assertEqual(
                os.path.normcase(entries[0]), os.path.normcase(str(library))
            )
            self.assertEqual(entries[1:], ["/existing/lib"])

    def test_lowercase_verdi_npi_library_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            verdi_home = Path(name)
            library = verdi_home / "share" / "NPI" / "lib" / "linux64"
            library.mkdir(parents=True)
            (library / "libNPI.so").touch()
            with patch.dict(
                os.environ,
                {
                    "VERDI_HOME": str(verdi_home),
                    "NPI_PLATFORM": "LINUX64",
                    "LD_LIBRARY_PATH": "/existing/lib",
                },
                clear=True,
            ):
                environment = _collector_environment()
            entries = environment["LD_LIBRARY_PATH"].split(os.pathsep)
            self.assertEqual(
                os.path.normcase(entries[0]), os.path.normcase(str(library))
            )
            self.assertEqual(entries[1:], ["/existing/lib"])

    def test_explicit_missing_npi_library_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            missing = Path(name) / "missing"
            with self.assertRaisesRegex(InventoryError, "not found"):
                _collector_environment(missing)

    def test_missing_elaborated_database_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "database not found"):
                collect_inventory(
                    collector,
                    [self._spec()],
                    RtlConfig(),
                    elab_db=root / "missing",
                )

    def test_elaborated_database_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "must be a directory"):
                collect_inventory(
                    collector,
                    [self._spec()],
                    RtlConfig(),
                    elab_db=elab_db,
                )

    def test_work_library_and_symlink_alias_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            work_library = root / "work.lib++"
            work_library.mkdir()
            candidates = [work_library]
            alias = root / "renamed-kdb"
            try:
                alias.symlink_to(work_library, target_is_directory=True)
            except OSError:
                pass
            else:
                candidates.append(alias)

            for candidate in candidates:
                with self.subTest(candidate=candidate), self.assertRaisesRegex(
                    InventoryError, "not an elaborated KDB"
                ):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=candidate,
                    )

    def test_existing_elaborated_database_is_the_only_design_argument(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "custom_kdb_name"
            elab_db.mkdir()
            observed_trace_rules = []

            def completed(command, **kwargs):
                self.assertEqual(Path(command[2]).read_text("utf-8"), "top.u\n")
                self.assertEqual(Path(kwargs["cwd"]), Path(command[2]).parent)
                trace_rules = Path(command[command.index("--trace-rules") + 1])
                observed_trace_rules.append(trace_rules.read_text("utf-8"))
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": 3,
                            "positions": {
                                "top.u": {"found": False, "instances": []}
                            },
                            "warnings": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed) as run:
                inventory = collect_inventory(
                    collector,
                    [self._spec()],
                    RtlConfig(),
                    elab_db=elab_db,
                )

            command = run.call_args[0][0]
            self.assertEqual(command[0], str(collector.resolve()))
            self.assertEqual(command[1], "--positions")
            self.assertEqual(command[3], "--output")
            self.assertEqual(observed_trace_rules, ["rs_pipe\tclk\n"])
            self.assertEqual(
                command[command.index("--trace-max-depth") + 1], "16"
            )
            self.assertEqual(
                command[command.index("--clk-port") :],
                [
                    "--clk-port",
                    "clk",
                    "--rst-port",
                    "rst",
                    "--elab-db",
                    str(elab_db.resolve()),
                ],
            )
            self.assertNotIn("--", command)
            self.assertFalse(inventory.positions["top.u"].found)

    def test_trace_rules_use_module_specific_clock_port_and_depth(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()
            observed = {}

            def completed(command, **kwargs):
                rules_path = Path(command[command.index("--trace-rules") + 1])
                observed["rules"] = rules_path.read_text("utf-8")
                observed["depth"] = command[
                    command.index("--trace-max-depth") + 1
                ]
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": 3,
                            "positions": {
                                "top.u": {"found": False, "instances": []}
                            },
                            "warnings": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            rule = ModuleRule(
                name="rs_pipe",
                has_rs_cfg_en=True,
                clk_port="pipe_clock",
                rst_port="pipe_reset_n",
            )
            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed):
                collect_inventory(
                    collector,
                    [self._spec(), self._spec()],
                    RtlConfig(crg_trace_max_depth=31),
                    {"rs_pipe": rule},
                    elab_db=elab_db,
                )

            self.assertEqual(observed, {"rules": "rs_pipe\tpipe_clock\n", "depth": "31"})

    def test_live_collector_legacy_inventory_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()

            def completed(command, **kwargs):
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "positions": {
                                "top.u": {"found": False, "instances": []}
                            },
                            "warnings": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed):
                with self.assertRaisesRegex(
                    InventoryError, "legacy inventory schema_version 2"
                ):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=elab_db,
                    )

    def test_trace_depth_is_defensively_bounded_for_direct_callers(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()
            for depth in (0, 257, True):
                with self.subTest(depth=depth), self.assertRaisesRegex(
                    InventoryError, "between 1 and 256"
                ):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(crg_trace_max_depth=depth),
                        elab_db=elab_db,
                    )

    def test_non_utf8_collector_output_is_safely_decoded(self) -> None:
        for stream_name in ("stdout", "stderr"):
            with self.subTest(stream=stream_name):
                with tempfile.TemporaryDirectory() as name:
                    root = Path(name)
                    collector = root / "collector"
                    collector.write_text("", encoding="utf-8")
                    elab_db = root / "kdb.elab++"
                    elab_db.mkdir()
                    invalid = b"collector failure: \xff"
                    completed = subprocess.CompletedProcess(
                        [],
                        11,
                        stdout=invalid if stream_name == "stdout" else b"",
                        stderr=invalid if stream_name == "stderr" else b"",
                    )

                    def run_collector(command, **kwargs):
                        self.assertNotIn("text", kwargs)
                        self.assertNotIn("encoding", kwargs)
                        return completed

                    with patch(
                        "rscheck.npi_runner.subprocess.run",
                        side_effect=run_collector,
                    ):
                        with self.assertRaises(InventoryError) as raised:
                            collect_inventory(
                                collector,
                                [self._spec()],
                                RtlConfig(),
                                elab_db=elab_db,
                            )

                    message = str(raised.exception)
                    self.assertIn("exited with code 11", message)
                    self.assertIn("collector failure: \ufffd", message)

    def test_collector_failure_preserves_stdout_and_stderr_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()
            completed = subprocess.CompletedProcess(
                [],
                11,
                stdout=b"compiler.log: unresolved module",
                stderr=b"error[NPI_LOAD]: no queryable top",
            )

            with patch("rscheck.npi_runner.subprocess.run", return_value=completed):
                with self.assertRaises(InventoryError) as raised:
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=elab_db,
                    )

            message = str(raised.exception)
            self.assertIn("stdout:\ncompiler.log: unresolved module", message)
            self.assertIn("stderr:\nerror[NPI_LOAD]: no queryable top", message)

    def test_partial_load_preserves_streams_and_compiler_log(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()

            def completed(command, **kwargs):
                temp_dir = Path(kwargs["cwd"])
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": 3,
                            "positions": {
                                "top.u": {"found": False, "instances": []}
                            },
                            "warnings": [],
                            "notices": ["top remains queryable after partial load"],
                        }
                    ),
                    encoding="utf-8",
                )
                log_dir = temp_dir / "collectorLog"
                log_dir.mkdir()
                (log_dir / "compiler.log").write_text(
                    "elaboration error detail", encoding="utf-8"
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=b"Total 1 error",
                    stderr=b"warning[NPI_LOAD_PARTIAL]",
                )

            diagnostic = StringIO()
            with patch(
                "rscheck.npi_runner.subprocess.run", side_effect=completed
            ), redirect_stderr(diagnostic):
                inventory = collect_inventory(
                    collector,
                    [self._spec()],
                    RtlConfig(),
                    elab_db=elab_db,
                )

            self.assertEqual(
                inventory.notices, ("top remains queryable after partial load",)
            )
            message = diagnostic.getvalue()
            self.assertIn("stdout:\nTotal 1 error", message)
            self.assertIn("stderr:\nwarning[NPI_LOAD_PARTIAL]", message)
            self.assertIn("collectorLog", message)
            self.assertIn("elaboration error detail", message)

    def test_subprocess_unicode_error_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()
            decode_error = UnicodeDecodeError(
                "utf-8", b"\xff", 0, 1, "invalid start byte"
            )

            with patch(
                "rscheck.npi_runner.subprocess.run",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(InventoryError, "failed to decode"):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=elab_db,
                    )

    def test_temporary_directory_creation_error_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()

            with patch(
                "rscheck.npi_runner.tempfile.TemporaryDirectory",
                side_effect=OSError("temporary storage unavailable"),
            ):
                with self.assertRaisesRegex(InventoryError, "temporary NPI files"):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=elab_db,
                    )

    def test_positions_write_error_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()

            with patch(
                "rscheck.npi_runner.Path.write_text",
                side_effect=OSError("temporary storage is full"),
            ), patch("rscheck.npi_runner.subprocess.run") as run:
                with self.assertRaisesRegex(InventoryError, "temporary NPI files"):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=elab_db,
                    )
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
