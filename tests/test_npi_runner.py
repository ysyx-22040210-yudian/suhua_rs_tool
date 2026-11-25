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
from rscheck.npi_runner import (
    _collector_environment,
    collect_hierarchy,
    collect_inventory,
    search_hierarchy,
)


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
        self.assertIn('name == "clk" || (!state->is_reset && name == "rst_n")', source)
        self.assertIn("intermediate port(s) with unknown direction", source)
        self.assertIn("mark_trace_unresolved(state, message.str())", source)
        self.assertIn("depth >= state->trace.max_depth", source)
        self.assertIn('state.trace.status = "depth_limited"', source)
        self.assertIn('state.trace.status = "unresolved"', source)
        self.assertIn('\\"schema_version\\": 4', source)
        self.assertIn('\\"clock_trace\\": ', source)
        self.assertIn(
            '\\"excluded_inputs\\": [\\"clk\\", \\"rst_n\\"]', source
        )
        self.assertIn("result.clk_sources = result.clock_trace.modules", source)

    def test_collector_has_bounded_hierarchy_listing_mode(self) -> None:
        source = (self.project_root / "npi" / "rs_npi_collector.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn('argument == "--list-hierarchy"', source)
        self.assertIn("kMaxHierarchyDepth = 256", source)
        self.assertIn("kMaxHierarchyScopeVisits = 1000000", source)
        self.assertIn(
            '"--list-hierarchy cannot be combined with --positions, "', source
        )

        start = source.index("class HierarchyCollector {")
        end = source.index("class Collector {", start)
        body = source[start:end]
        self.assertIn("npi_iterate(npiInstance, NULL)", body)
        self.assertIn("npi_iterate(npiInternalScope, scope)", body)
        self.assertIn("const bool display_scope = supported_scope_type(type);", body)
        self.assertIn("if (display_scope) {", body)
        for scope_type in (
            "npiModule",
            "npiInterface",
            "npiProgram",
            "npiGenScope",
        ):
            self.assertIn(scope_type, body)
        self.assertIn("std::sort(result_.scopes.begin()", body)
        self.assertIn("std::unique(result_.scopes.begin()", body)
        self.assertIn("NPI hierarchy has no queryable top scope", body)
        self.assertIn("if (result_.scopes.empty()) {", body)
        self.assertIn("scope_visit_limit_reached_ = true", body)
        self.assertGreaterEqual(
            body.count("npi_release_handle(iterator);"), 2
        )
        self.assertNotIn("build_instance(", body)
        self.assertNotIn("collect_ports(", body)
        self.assertNotIn("collect_parameters(", body)
        self.assertNotIn("trace_clock(", body)

        writer_start = source.index("bool write_hierarchy(")
        writer_end = source.index("void write_string_array(", writer_start)
        writer = source[writer_start:writer_end]
        for field in (
            '\\"schema\\": \\"rscheck.hierarchy\\"',
            '\\"schema_version\\": 1',
            '\\"scopes\\"',
            '\\"path\\"',
            '\\"name\\"',
            '\\"definition\\"',
            '\\"kind\\"',
            '\\"warnings\\"',
            '\\"notices\\"',
            '\\"truncated\\"',
            '\\"visited_scopes\\"',
        ):
            self.assertIn(field, writer)

        run_start = source.index("int run(int argc, char** argv)")
        run_body = source[run_start:]
        self.assertIn("if (!options.list_hierarchy) {", run_body)
        self.assertIn("if (options.list_hierarchy) {", run_body)
        self.assertIn("HierarchyCollector hierarchy_collector;", run_body)
        self.assertIn("write_hierarchy(options.output_path", run_body)
        self.assertIn("bool partial_load = false;", run_body)
        self.assertIn("if (partial_load) {", run_body)
        self.assertIn("hierarchy.truncated = true;", run_body)
        self.assertLess(
            run_body.index("npi_load_design(npi_argc, npi_argv)"),
            run_body.index("HierarchyCollector hierarchy_collector;"),
        )

    def test_clock_trace_cache_isolated_by_instance_hierarchy(self) -> None:
        source = (self.project_root / "npi" / "rs_npi_collector.cpp").read_text(
            encoding="utf-8"
        )
        start = source.index("  ClockTrace trace_clock(")
        end = source.index("  InstanceInfo build_instance(", start)
        body = source[start:end]
        self.assertIn(
            'instance_full_name + "\\x1f" + clock_port + "\\x1f"',
            body,
        )
        self.assertNotIn(
            'std::string cache_key = clock_port + "\\x1f";', body
        )

    def test_partial_kdb_always_merges_language_model_input_ports(self) -> None:
        source = (self.project_root / "npi" / "rs_npi_collector.cpp").read_text(
            encoding="utf-8"
        )
        start = source.index("  void expand_module_inputs(")
        end = source.index("  ClockTrace trace_clock(", start)
        body = source[start:end]
        fallback_call = "trace_language_input_ports("
        fallback_guard = "if (!fallback_available &&"
        self.assertIn("const bool fallback_available", body)
        self.assertIn(fallback_call, body)
        self.assertIn(fallback_guard, body)
        self.assertLess(body.index(fallback_call), body.index(fallback_guard))
        self.assertIn("netlist_classified_ports", body)
        self.assertIn("netlist_resolved_ports", body)
        self.assertIn("netlist_unknown_ports", body)
        self.assertIn("fallback_classified_ports.find(*name)", body)
        self.assertNotIn(
            "if (scanned_ports == 0 || unknown_directions != 0) {", body
        )

        fallback_start = source.index("  bool trace_language_input_ports(")
        fallback_end = source.index(
            "  void trace_netlist_input_relation(", fallback_start
        )
        fallback_body = source[fallback_start:fallback_end]
        self.assertIn("npi_iterate(npiPort, module)", fallback_body)
        self.assertIn(
            "npi_mod_inst_get_port(&mutable_full_name[0], fallback_ports)",
            fallback_body,
        )
        self.assertLess(
            fallback_body.index("netlist_resolved_ports.find(port->first)"),
            fallback_body.index("if (!port->second.has_direction)"),
        )

        netlist_start = source.index("  void trace_netlist_input_relation(")
        netlist_end = source.index("  void expand_module_inputs(", netlist_start)
        netlist_body = source[netlist_start:netlist_end]
        self.assertIn("if (!hits.empty() && !name.empty())", netlist_body)
        self.assertIn("resolved_ports->insert(name)", netlist_body)

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
    @staticmethod
    def _hierarchy_payload(*, truncated: bool = False) -> dict:
        return {
            "schema": "rscheck.hierarchy",
            "schema_version": 1,
            "scopes": [
                {
                    "path": "top.u_other",
                    "name": "u_other",
                    "definition": "other",
                    "kind": "module",
                },
                {
                    "path": "top.u_tile",
                    "name": "u_tile",
                    "definition": "tile",
                    "kind": "module",
                },
                {
                    "path": "top.u_tile.u_peer",
                    "name": "u_peer",
                    "definition": "peer",
                    "kind": "module",
                },
            ],
            "warnings": ["index is bounded"] if truncated else [],
            "notices": [],
            "truncated": truncated,
            "visited_scopes": 3,
        }

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

    def test_hierarchy_search_uses_dedicated_collector_mode_and_python_regex(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "design.elab++"
            elab_db.mkdir()
            observed = {}

            def completed(command, **kwargs):
                observed["command"] = command
                observed["kwargs"] = kwargs
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(self._hierarchy_payload()), encoding="utf-8"
                )
                return subprocess.CompletedProcess(
                    command, 0, stdout=b"", stderr=b""
                )

            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed):
                result = search_hierarchy(
                    collector,
                    r"(?i)U_(TILE|PEER)",
                    elab_db=elab_db,
                    limit=1,
                    timeout_seconds=45,
                )

            self.assertEqual(result.total_matches, 2)
            self.assertEqual([item.path for item in result.matches], ["top.u_tile"])
            self.assertTrue(result.truncated)
            self.assertFalse(result.index_truncated)
            command = observed["command"]
            self.assertEqual(
                command,
                [
                    str(collector.resolve()),
                    "--list-hierarchy",
                    "--output",
                    command[3],
                    "--elab-db",
                    str(elab_db.resolve()),
                ],
            )
            self.assertNotIn("--positions", command)
            self.assertEqual(observed["kwargs"]["timeout"], 45)
            self.assertEqual(observed["kwargs"]["cwd"], str(Path(command[3]).parent))
            self.assertFalse(observed["kwargs"].get("text", False))

    def test_hierarchy_search_no_match_and_exact_limit_are_not_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "design.elab++"
            elab_db.mkdir()

            def completed(command, **kwargs):
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(self._hierarchy_payload()), encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed):
                exact = search_hierarchy(
                    collector, r"u_tile", elab_db=elab_db, limit=2
                )
                missing = search_hierarchy(
                    collector, r"does_not_exist", elab_db=elab_db, limit=2
                )
            self.assertEqual(exact.total_matches, 2)
            self.assertEqual(len(exact.matches), 2)
            self.assertFalse(exact.truncated)
            self.assertEqual(missing.matches, ())
            self.assertFalse(missing.truncated)

    def test_hierarchy_invalid_regex_fails_before_collector(self) -> None:
        with patch("rscheck.npi_runner.collect_hierarchy") as collect:
            for query in ("", "   ", "[", "line\nnext", "x" * 257):
                with self.subTest(query=query), self.assertRaises(InventoryError):
                    search_hierarchy(
                        "collector", query, elab_db="design.elab++"
                    )
            collect.assert_not_called()

    def test_hierarchy_index_rejects_unsorted_duplicate_or_malformed_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "design.elab++"
            elab_db.mkdir()
            payload = self._hierarchy_payload()
            payload["scopes"].reverse()

            def completed(command, **kwargs):
                output = Path(command[command.index("--output") + 1])
                output.write_text(json.dumps(payload), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed):
                with self.assertRaisesRegex(InventoryError, "sorted"):
                    collect_hierarchy(collector, elab_db=elab_db)

            payload = self._hierarchy_payload()
            payload["scopes"][1] = dict(payload["scopes"][0])
            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed):
                with self.assertRaisesRegex(InventoryError, "sorted"):
                    collect_hierarchy(collector, elab_db=elab_db)

            payload = self._hierarchy_payload()
            payload["scopes"][0]["kind"] = "net"
            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed):
                with self.assertRaisesRegex(InventoryError, "kind"):
                    collect_hierarchy(collector, elab_db=elab_db)

    def test_hierarchy_index_truncation_is_propagated_to_search(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "design.elab++"
            elab_db.mkdir()

            def completed(command, **kwargs):
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(self._hierarchy_payload(truncated=True)),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed):
                result = search_hierarchy(
                    collector, r"u_other", elab_db=elab_db, limit=500
                )
            self.assertEqual(result.total_matches, 1)
            self.assertTrue(result.index_truncated)
            self.assertTrue(result.truncated)
            self.assertEqual(result.warnings, ("index is bounded",))

    def test_hierarchy_collection_validates_live_inputs_before_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            missing_collector = root / "missing-collector"
            elab_db = root / "design.elab++"
            elab_db.mkdir()
            with patch("rscheck.npi_runner.subprocess.run") as run:
                with self.assertRaisesRegex(InventoryError, "collector executable"):
                    collect_hierarchy(missing_collector, elab_db=elab_db)
                run.assert_not_called()

            collector.write_text("", encoding="utf-8")
            elab_file = root / "file.elab++"
            elab_file.write_text("not a KDB", encoding="utf-8")
            work_library = root / "work.lib++"
            work_library.mkdir()
            with patch("rscheck.npi_runner.subprocess.run") as run:
                with self.assertRaisesRegex(InventoryError, "must be a directory"):
                    collect_hierarchy(collector, elab_db=elab_file)
                with self.assertRaisesRegex(InventoryError, "compiled Verdi library"):
                    collect_hierarchy(collector, elab_db=work_library)
                run.assert_not_called()

            collector_resolved = collector.resolve()
            with patch(
                "rscheck.npi_runner.Path.resolve",
                side_effect=[collector_resolved, OSError("symlink loop")],
            ), patch("rscheck.npi_runner.subprocess.run") as run:
                with self.assertRaisesRegex(
                    InventoryError, "cannot resolve Verdi elaborated database"
                ):
                    collect_hierarchy(collector, elab_db=elab_db)
                run.assert_not_called()

    def test_hierarchy_collector_failures_are_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "design.elab++"
            elab_db.mkdir()

            with patch(
                "rscheck.npi_runner.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["collector"], 3),
            ):
                with self.assertRaisesRegex(InventoryError, "timed out after 3"):
                    collect_hierarchy(
                        collector, elab_db=elab_db, timeout_seconds=3
                    )

            failed = subprocess.CompletedProcess(
                ["collector"], 11, stdout=b"load stdout", stderr=b"load stderr"
            )
            with patch("rscheck.npi_runner.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(InventoryError, "code 11") as raised:
                    collect_hierarchy(collector, elab_db=elab_db)
            self.assertIn("load stdout", str(raised.exception))
            self.assertIn("load stderr", str(raised.exception))

            succeeded = subprocess.CompletedProcess(
                ["collector"], 0, stdout=b"", stderr=b""
            )
            with patch("rscheck.npi_runner.subprocess.run", return_value=succeeded):
                with self.assertRaisesRegex(InventoryError, "did not create"):
                    collect_hierarchy(collector, elab_db=elab_db)

            def invalid_json(command, **kwargs):
                output = Path(command[command.index("--output") + 1])
                output.write_bytes(b"\xff")
                return succeeded

            with patch(
                "rscheck.npi_runner.subprocess.run", side_effect=invalid_json
            ):
                with self.assertRaisesRegex(InventoryError, "cannot read"):
                    collect_hierarchy(collector, elab_db=elab_db)

    def test_hierarchy_partial_load_notice_preserves_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "design.elab++"
            elab_db.mkdir()
            payload = self._hierarchy_payload()
            payload["notices"] = ["top remains queryable after partial load"]

            def completed(command, **kwargs):
                temp_dir = Path(kwargs["cwd"])
                output = Path(command[command.index("--output") + 1])
                output.write_text(json.dumps(payload), encoding="utf-8")
                log_dir = temp_dir / "collectorLog"
                log_dir.mkdir()
                (log_dir / "compiler.log").write_text(
                    "elaboration detail", encoding="utf-8"
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=b"partial stdout",
                    stderr=b"warning[NPI_LOAD_PARTIAL]",
                )

            diagnostic = StringIO()
            with patch(
                "rscheck.npi_runner.subprocess.run", side_effect=completed
            ), redirect_stderr(diagnostic):
                hierarchy = collect_hierarchy(collector, elab_db=elab_db)
            self.assertEqual(
                hierarchy.notices, ("top remains queryable after partial load",)
            )
            self.assertIn("partial stdout", diagnostic.getvalue())
            self.assertIn("warning[NPI_LOAD_PARTIAL]", diagnostic.getvalue())
            self.assertIn("elaboration detail", diagnostic.getvalue())

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
                            "schema_version": 4,
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
            self.assertEqual(observed_trace_rules, ["rs_pipe\tclk\trst_n\n"])
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
                            "schema_version": 4,
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

            self.assertEqual(observed, {"rules": "rs_pipe\tpipe_clock\tpipe_reset_n\n", "depth": "31"})

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
                            "schema_version": 4,
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
