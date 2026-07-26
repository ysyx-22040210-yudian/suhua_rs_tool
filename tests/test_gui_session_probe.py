from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test_vm_verdi_gui.sh"
GUI_SMOKE_SCRIPT = ROOT / "scripts" / "test_rscheck_gui_smoke.py"
SITE_ENV_RUNNER = ROOT / "scripts" / "lib" / "run_with_env_file.sh"
BASH = shutil.which("bash")


class VmVerdiReadinessContractTests(unittest.TestCase):
    def test_only_matching_elaborated_top_title_can_be_ready(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("VERDI_GENERIC_READY_DELAY", source)
        self.assertNotIn("VERDI_TITLE_CONFIRMED", source)
        self.assertNotIn('[ -s "$NEW_WINDOWS" ] ||', source)
        self.assertNotIn("title format differs", source)
        self.assertGreaterEqual(
            source.count('grep -Eq "$VERDI_READY_REGEX" "$NEW_WINDOWS"'),
            2,
        )
        self.assertIn(
            "no new Verdi X11 window title matched VERDI_READY_REGEX",
            source,
        )
        self.assertIn("Verdi GUI loaded elaborated top 'top'", source)

    def test_gui_smoke_contract_fields_are_order_independent(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn(
            "contract=elab-only schemas=report-v4/inventory-v3",
            source,
        )
        self.assertGreaterEqual(source.count("grep -Fq 'contract=elab-only'"), 2)
        self.assertGreaterEqual(
            source.count("grep -Fq 'schemas=report-v4/inventory-v3'"),
            4,
        )
        self.assertNotIn("report-v3/inventory-v2", source)

    def test_vm_flow_requires_arbitrary_header_column_mapping_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"接口分类"', source)
        self.assertIn('"假门控标记"', source)
        self.assertIn(
            "strict header validation must be disabled by default",
            source,
        )
        self.assertIn(
            "column mapping evidence OK: arbitrary headers -> internal fields",
            source,
        )
        self.assertIn(
            "grep -Fq 'header-map=column-index strict-header=false'",
            source,
        )

    def test_vm_flow_requires_complete_config_io_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        gui_smoke = GUI_SMOKE_SCRIPT.read_text(encoding="utf-8")
        marker = (
            "config-io=roundtrip-complete "
            "roots=excel,columns,rtl,position_mappings,"
            "crg_source_mappings,module_rules"
        )

        self.assertIn(marker, gui_smoke)
        self.assertIn(marker, source)
        self.assertIn("app._export_config()", gui_smoke)
        self.assertIn("app._import_config()", gui_smoke)
        self.assertIn("app._apply_crg_source_mapping()", gui_smoke)
        self.assertIn("app._save_crg_source_mappings()", gui_smoke)
        self.assertIn("app._delete_crg_source_mapping()", gui_smoke)
        self.assertIn('"crg-sources": app.crg_sources_tab', gui_smoke)
        self.assertIn('!= "CRG Source映射库"', gui_smoke)
        self.assertIn('!= "CRG Source简称"', gui_smoke)
        self.assertIn('!= "RTL完整路径"', gui_smoke)
        self.assertIn("expected_exported_crg_source_mappings", gui_smoke)
        self.assertIn(
            "GUI config export did not preserve the complete CRG_source database",
            gui_smoke,
        )
        self.assertIn(
            "GUI config import did not restore the complete CRG_source database",
            gui_smoke,
        )
        self.assertIn("crg-source-db=crud-complete", gui_smoke)
        self.assertIn("dirty-copy=export-preserved/import-cleared", gui_smoke)

    def test_vm_flow_requires_partial_npi_load_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        gui_smoke = GUI_SMOKE_SCRIPT.read_text(encoding="utf-8")
        rtl = (ROOT / "examples" / "rtl" / "rs_example.sv").read_text(
            encoding="utf-8"
        )

        self.assertIn("RSCHECK_PARTIAL_LOAD_FIXTURE", rtl)
        self.assertIn("warning[NPI_LOAD_PARTIAL]", source)
        self.assertIn('inventory.get("notices")', source)
        self.assertIn("RESULT: PASS | rows=2 errors=0 warnings=1", source)
        self.assertIn("--expect-partial-load", source)
        self.assertIn("notice=NPI_LOAD_PARTIAL", source)
        self.assertGreaterEqual(gui_smoke.count('if iid == "global":'), 2)

    def test_vm_flow_requires_npi_l1_formal_port_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("NPI_L1_INC_DIR", source)
        self.assertIn("libnpiL1.so", source)
        self.assertIn("partial-load formal-port inventory mismatch", source)
        self.assertIn('{"CTRL_RS_D0", "AAAA_BBB_C0", "CLK_ONLY_RS"}', source)
        self.assertIn("partial clk-only module mismatch", source)
        self.assertIn("partial clk-only formal ports mismatch", source)
        self.assertIn("partial clk-only high connection mismatch", source)
        self.assertIn("partial clk-only unexpectedly has an rst formal port", source)
        self.assertIn("partial clk-present/rst-absent evidence OK", source)
        self.assertIn("partial NPI formal-port L0/L1 inventory evidence OK", source)
        self.assertNotIn("clock-source tracing must be disabled", source)
        self.assertIn("custom module clk/rst formal-port rule evidence OK", source)
        self.assertIn(
            "custom clock_i trace warning evidence OK: CRG_SOURCE_NOT_FOUND",
            source,
        )
        self.assertIn("online_gui_custom_port.log", source)
        self.assertIn("mode=online case=custom-port", source)
        self.assertIn("rule-ports=clock_i/reset_ni", source)
        self.assertIn('"rs_clk_only": {"clk", "d", "q"}', source)
        self.assertIn("online_gui_clk_present_rst_missing.log", source)
        self.assertIn("mode=online case=clk-present-rst-missing", source)
        self.assertIn("finding isolation OK: RST_PORT_MISSING only", source)
        self.assertIn("clk-port-evidence=present", source)
        self.assertIn("finding-codes=RST_PORT_MISSING", source)
        self.assertIn("GUI_CLK_WITHOUT_RST_ITERATIONS", source)

    def test_vm_flow_requires_bounded_recursive_crg_trace_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        gui_smoke = GUI_SMOKE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "printf 'rs_pipe\\tclk\\nrs_custom\\tclock_i\\n"
            "rs_clk_only\\tclk\\n'",
            source,
        )
        self.assertIn("partial_load_trace_rules.tsv", source)
        self.assertIn("clean_load_trace_rules.tsv", source)
        self.assertGreaterEqual(source.count("--trace-rules"), 2)
        self.assertGreaterEqual(source.count("--trace-max-depth 16"), 2)
        self.assertIn('or trace.get("excluded_inputs") != ["clk", "rst_n"]', source)
        self.assertIn('occ = "top.u_tile.u_occ"', source)
        self.assertIn('mux = "top.u_tile.u_clk_mux"', source)
        self.assertIn('core = "top.u_tile.u_crg"', source)
        self.assertIn('aux = "top.u_tile.u_aux_crg"', source)
        self.assertIn('assert_trace("CUSTOM_RS", "clock_i"', source)
        self.assertIn("--crg-trace-max-depth 3", source)
        self.assertIn(
            "CRG recursion evidence OK: depth=3, branched path accepts exact "
            "full-path match",
            source,
        )
        self.assertIn('report.get("schema_version") != 4', source)
        self.assertIn('inventory.get("schema_version") != 3', source)

        self.assertIn(
            'GUI_CRG_TRACE_DEPTH_ITERATIONS="${GUI_CRG_TRACE_DEPTH_ITERATIONS:-20}"',
            source,
        )
        self.assertIn("online_gui_crg_trace_depth_limit.log", source)
        self.assertIn("--crg-depth-limit", source)
        self.assertIn("--crg-depth-limit", gui_smoke)
        depth_marker = (
            "crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6"
        )
        self.assertIn(depth_marker, source)
        self.assertIn(depth_marker, gui_smoke)
        self.assertIn(
            "warnings=警告 6 mode=online case=crg-depth-limit",
            source,
        )

        custom_marker = (
            "crg-source-check=warning finding-code=CRG_SOURCE_NOT_FOUND"
        )
        self.assertIn(custom_marker, source)
        self.assertIn(custom_marker, gui_smoke)
        self.assertIn(
            "warnings=警告 1 mode=online case=custom-port",
            source,
        )
        self.assertIn(
            "clk-present/rst-missing CRG trace evidence OK: pass with no warning",
            source,
        )
        self.assertIn(
            "online GUI clk-present/rst-missing case emitted a CRG trace warning",
            source,
        )

        unified_log_gate = source.split("for gui_log in \\", 1)[1].split(
            "done", 1
        )[0]
        log_list = unified_log_gate.split("; do", 1)[0]
        log_entries = [
            line
            for line in log_list.splitlines()
            if line.strip().startswith('"$')
        ]
        self.assertEqual(len(log_entries), 12)
        self.assertIn('"$CRG_TRACE_DEPTH_LOG"', unified_log_gate)
        self.assertIn(
            "grep -Fq 'schemas=report-v4/inventory-v3' \"$gui_log\"",
            unified_log_gate,
        )
        gui_logs_line = next(
            line for line in source.splitlines() if line.startswith('echo "GUI_LOGS=')
        )
        self.assertIn("$CRG_TRACE_DEPTH_LOG", gui_logs_line)
        gui_log_vars = gui_logs_line.split("=", 1)[1].rstrip('"').split(",")
        self.assertEqual(len(gui_log_vars), 12)

    def test_vm_flow_requires_rs_cfg_dontcare_gui_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        gui_smoke = GUI_SMOKE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--rs-cfg-dontcare", source)
        self.assertIn("--rs-cfg-dontcare", gui_smoke)
        self.assertIn("GUI_RS_CFG_DONTCARE_ITERATIONS", source)
        self.assertIn("offline_gui_rs_cfg_dontcare.log", source)
        marker = (
            "has-rs-cfg-en=false label=dont-care "
            "rs-crg-en=absent findings=none"
        )
        self.assertIn(marker, source)
        self.assertIn(marker, gui_smoke)
        self.assertIn("parsed-rs-cfg-en=任意非标准文本", gui_smoke)

    def test_vm_flow_requires_rs_cfg_na_gui_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        gui_smoke = GUI_SMOKE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--rs-cfg-na", source)
        self.assertIn("--rs-cfg-na", gui_smoke)
        self.assertIn("GUI_RS_CFG_NA_ITERATIONS", source)
        self.assertIn(
            'GUI_RS_CFG_NA_ITERATIONS="${GUI_RS_CFG_NA_ITERATIONS:-20}"',
            source,
        )
        self.assertIn("offline_gui_rs_cfg_na.log", source)
        marker = (
            "rs-cfg-en=NA check=skipped "
            "rtl-rs-crg-en=1 findings=none"
        )
        self.assertIn(marker, source)
        self.assertIn(marker, gui_smoke)
        self.assertIn('"RS_CRG_EN": "1"', gui_smoke)
        self.assertIn(
            'report_spec.get("RS_CFG_EN") != _RS_CFG_NA_TEXT', gui_smoke
        )
        self.assertIn(
            'csv_rows[0].get("RS_CFG_EN") != _RS_CFG_NA_TEXT', gui_smoke
        )
        self.assertIn(
            '"--rs-cfg-na is mutually exclusive with all other special modes"',
            gui_smoke,
        )
        unified_log_gate = source.split("for gui_log in \\", 1)[1].split(
            "done", 1
        )[0]
        gui_logs_line = next(
            line for line in source.splitlines() if line.startswith('echo "GUI_LOGS=')
        )
        self.assertIn('"$RS_CFG_NA_LOG"', unified_log_gate)
        self.assertIn("$RS_CFG_NA_LOG", gui_logs_line)
        self.assertIn(
            "grep -Fq 'module-rule-ports=preserved' \"$gui_log\"",
            unified_log_gate,
        )
        self.assertIn(
            "offline GUI RS_CFG_EN=NA case emitted an RS_CFG_EN_* finding",
            source,
        )

    def test_vm_flow_requires_crg_source_mapping_gui_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        gui_smoke = GUI_SMOKE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--crg-source-mapping", source)
        self.assertIn("--crg-source-mapping", gui_smoke)
        self.assertIn("GUI_CRG_SOURCE_MAPPING_ITERATIONS", source)
        self.assertIn(
            'GUI_CRG_SOURCE_MAPPING_ITERATIONS="${GUI_CRG_SOURCE_MAPPING_ITERATIONS:-20}"',
            source,
        )
        self.assertIn("offline_gui_crg_source_mapping.log", source)
        marker = (
            "crg-source-map=core_clock_source->top.u_soc.u_crg_core "
            "gui-json-csv=alias+full crg-source-check=pass "
            "trace-depth=3 findings=none"
        )
        self.assertIn(marker, source)
        self.assertIn(marker, gui_smoke)
        self.assertIn(
            'report_spec.get("CRG_source") != _CRG_SOURCE_FULL_PATH',
            gui_smoke,
        )
        self.assertIn(
            'report_spec.get("crg_source_alias") != _CRG_SOURCE_ALIAS',
            gui_smoke,
        )
        self.assertIn(
            'csv_rows[0].get("CRG_source") != _CRG_SOURCE_FULL_PATH',
            gui_smoke,
        )
        self.assertIn(
            'csv_rows[0].get("crg_source_alias") != _CRG_SOURCE_ALIAS',
            gui_smoke,
        )
        self.assertIn(
            '"--crg-source-mapping is mutually exclusive with all other special modes"',
            gui_smoke,
        )
        unified_log_gate = source.split("for gui_log in \\", 1)[1].split(
            "done", 1
        )[0]
        gui_logs_line = next(
            line for line in source.splitlines() if line.startswith('echo "GUI_LOGS=')
        )
        self.assertIn('"$CRG_SOURCE_MAPPING_LOG"', unified_log_gate)
        self.assertIn("$CRG_SOURCE_MAPPING_LOG", gui_logs_line)
        self.assertIn("crg-source-db=crud-complete", unified_log_gate)
        self.assertIn(
            "dirty-copy=export-preserved/import-cleared", unified_log_gate
        )
        self.assertIn(
            "offline GUI CRG_source mapping case emitted a CRG_SOURCE_* finding",
            source,
        )

    def test_expected_clk_only_cli_failure_is_guarded_from_err_trap(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        block = source.split('CLK_ONLY_LOG="$TEST_ROOT/', 1)[1].split(
            'ONLINE_GUI_LOG="$TEST_ROOT/', 1
        )[0]

        self.assertNotIn("set +e", block)
        self.assertIn('if "$PYTHON_BIN" -m rscheck check \\', block)
        self.assertIn(
            '2>&1 | tee "$CLK_ONLY_LOG"; then\n'
            "  CLK_ONLY_CHECK_RC=0\n"
            "else\n"
            "  CLK_ONLY_CHECK_RC=$?\n"
            "fi",
            block,
        )

    @unittest.skipUnless(BASH, "Bash is required for ERR trap control-flow test")
    def test_guarded_expected_failure_preserves_pipeline_status(self) -> None:
        script = """\
set -Ee -o pipefail
trap 'printf "ERR_TRAP\\n" >&2; exit 97' ERR
if bash -c 'exit 1' 2>&1 | tee /dev/null; then
  rc=0
else
  rc=$?
fi
printf 'RC=%s\\n' "$rc"
[ "$rc" -eq 1 ]
"""

        result = subprocess.run(
            [BASH, "-c", script],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RC=1", result.stdout)
        self.assertNotIn("ERR_TRAP", result.stderr)

    def test_license_environment_import_contract_is_safe(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        runner = SITE_ENV_RUNNER.read_text(encoding="utf-8")
        helper = source.split(
            "# BEGIN VERDI LICENSE ENVIRONMENT HELPERS", 1
        )[1].split("# END VERDI LICENSE ENVIRONMENT HELPERS", 1)[0]

        self.assertIn('source "$RSCHECK_SITE_ENV_FILE"', runner)
        self.assertIn('"$SCRIPT_DIR/lib/run_with_env_file.sh"', source)
        self.assertIn(
            'runuser -u "$GUI_SESSION_SELECTED_USER" -- bash -lc', helper
        )
        self.assertIn("VERDI_AUTO_LICENSE_IMPORT", source)
        self.assertNotIn('"LM_LICENSE_FILE=$LM_LICENSE_FILE"', source)
        self.assertNotIn('"SNPSLMD_LICENSE_FILE=$SNPSLMD_LICENSE_FILE"', source)
        self.assertIn('"PROJECT_ROOT=$RSCHECK_FIXED_PROJECT_ROOT"', source)
        self.assertIn('"OUTPUT_BASE=$RSCHECK_FIXED_OUTPUT_BASE"', source)
        self.assertIn("-u SHELLOPTS", runner)
        self.assertIn("{ set +x; } 2>/dev/null", source)
        self.assertIn("{ set +x; } 2>/dev/null", runner)
        self.assertNotIn("/home/host", helper)
        self.assertNotIn("eval ", helper)
        self.assertNotIn(".bashrc", helper)
        probe_exit = source.index('if [ "$GUI_PROBE_ONLY" -eq 1 ]')
        import_call = source.index("\nresolve_verdi_license_environment\n", probe_exit)
        self.assertGreater(import_call, probe_exit)
        self.assertIn(
            '[ "${1-}" = "--gui-probe-only" ]', source
        )


@unittest.skipUnless(
    BASH and sys.platform.startswith("linux"),
    "Linux bash is required for site environment isolation tests",
)
class SiteEnvironmentRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run(
        self,
        env_content: str,
        command: list[str],
        **overrides: str,
    ) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        env_file = self.root / "site-env.sh"
        env_file.write_text(env_content, encoding="utf-8")
        environment = os.environ.copy()
        for name in (
            "BASH_ENV",
            "ENV",
            "SHELLOPTS",
            "BASHOPTS",
            "PS4",
            "BASH_XTRACEFD",
            "LM_LICENSE_FILE",
            "SNPSLMD_LICENSE_FILE",
        ):
            environment.pop(name, None)
        environment.update(overrides)
        return subprocess.run(
            [BASH, str(SITE_ENV_RUNNER), str(env_file), *command],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_site_environment_is_isolated_and_xtrace_cannot_leak(self) -> None:
        secret = "license-secret-must-not-appear"
        expected_file = self.root / "expected-license"
        expected_file.write_text(secret, encoding="utf-8")
        result = self._run(
            "export LM_LICENSE_FILE=" + secret + "\n"
            "export PROJECT_ROOT=/wrong/project\n"
            "export OUTPUT_BASE=/wrong/output\n"
            "PS4=\"$LM_LICENSE_FILE\"\n"
            "set -x\n"
            "trap 'printf \\\"%s\\\\n\\\" \\\"$LM_LICENSE_FILE\\\"' DEBUG RETURN EXIT\n"
            "cd /\n",
            [
                "/usr/bin/env",
                "PROJECT_ROOT=/verified/project",
                "OUTPUT_BASE=/verified/artifacts",
                BASH,
                "-c",
                'expected=$(cat "$TEST_EXPECTED_LICENSE_FILE") && '
                '[ "$PROJECT_ROOT" = /verified/project ] && '
                '[ "$OUTPUT_BASE" = /verified/artifacts ] && '
                '[ "$(pwd -P)" = "$TEST_EXPECT_CWD" ] && '
                '[ "$LM_LICENSE_FILE" = "$expected" ] && '
                "cmdline=$(tr '\\0' ' ' </proc/$$/cmdline) && "
                'case "$cmdline" in *"$expected"*) exit 91 ;; esac && '
                "printf 'CONTRACT_PASS\\n'",
            ],
            TEST_EXPECT_CWD=str(ROOT),
            TEST_EXPECTED_LICENSE_FILE=str(expected_file),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONTRACT_PASS", result.stdout)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn("/wrong/project", result.stdout + result.stderr)

    def test_failed_site_environment_never_launches_command(self) -> None:
        marker = self.root / "command-ran"
        result = self._run(
            "export LM_LICENSE_FILE=partial-secret\nreturn 23\n",
            [BASH, "-c", 'touch "$TEST_COMMAND_MARKER"'],
            TEST_COMMAND_MARKER=str(marker),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertNotIn("partial-secret", result.stdout + result.stderr)
        self.assertIn("initialization failed", result.stderr)

    def test_exit_exec_and_noexec_cannot_fake_success(self) -> None:
        for label, env_content in (
            ("exit-zero", "exit 0\n"),
            ("exec-true", "exec true\n"),
            ("noexec", "set -n\n"),
        ):
            with self.subTest(label=label):
                marker = self.root / (label + "-command-ran")
                result = self._run(
                    env_content,
                    [BASH, "-c", 'touch "$TEST_COMMAND_MARKER"'],
                    TEST_COMMAND_MARKER=str(marker),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())
                self.assertIn(
                    "before environment initialization completed", result.stderr
                )

    def test_existing_license_has_precedence_without_argv_exposure(self) -> None:
        existing = "existing-license-must-not-be-an-argument"
        expected_file = self.root / "expected-existing-license"
        expected_file.write_text(existing, encoding="utf-8")
        result = self._run(
            "export LM_LICENSE_FILE=site-replacement\n",
            [
                BASH,
                "-c",
                'expected=$(cat "$TEST_EXPECTED_LICENSE_FILE") && '
                '[ "$LM_LICENSE_FILE" = "$expected" ] && '
                "cmdline=$(tr '\\0' ' ' </proc/$$/cmdline) && "
                'case "$cmdline" in *"$expected"*) exit 92 ;; esac',
            ],
            LM_LICENSE_FILE=existing,
            TEST_EXPECTED_LICENSE_FILE=str(expected_file),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(existing, result.stdout + result.stderr)

    def test_startup_hooks_and_shell_options_are_removed(self) -> None:
        hook = self.root / "bash-env-hook.sh"
        marker = self.root / "bash-env-ran"
        hook.write_text('touch "$TEST_HOOK_MARKER"\n', encoding="utf-8")
        result = self._run(
            "export BASH_ENV=\"$TEST_HOOK_FILE\"\n"
            "export SHELLOPTS\n",
            [BASH, "-c", '[ ! -e "$TEST_HOOK_MARKER" ]'],
            TEST_HOOK_FILE=str(hook),
            TEST_HOOK_MARKER=str(marker),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_unset_exported_value_is_propagated(self) -> None:
        result = self._run(
            "unset TEST_STALE_VALUE\n",
            [BASH, "-c", '[ -z "${TEST_STALE_VALUE+x}" ]'],
            TEST_STALE_VALUE="stale",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_relative_site_environment_path_is_rejected(self) -> None:
        assert BASH is not None
        result = subprocess.run(
            [BASH, str(SITE_ENV_RUNNER), "relative-env.sh", "true"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute path", result.stderr)


@unittest.skipUnless(
    BASH and sys.platform.startswith("linux"),
    "Linux bash is required for Verdi license environment contract tests",
)
class VerdiLicenseEnvironmentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _command(self, name: str, content: str) -> Path:
        path = self.bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run_helper(
        self, body: str, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        source = SCRIPT.read_text(encoding="utf-8")
        helper = source.split(
            "# BEGIN VERDI LICENSE ENVIRONMENT HELPERS", 1
        )[1].split("# END VERDI LICENSE ENVIRONMENT HELPERS", 1)[0]
        environment = os.environ.copy()
        for name in (
            "BASH_ENV",
            "ENV",
            "LM_LICENSE_FILE",
            "SNPSLMD_LICENSE_FILE",
            "VERDI_ENV_FILE",
            "VERDI_AUTO_LICENSE_IMPORT",
            "GUI_SESSION_SELECTED_USER",
        ):
            environment.pop(name, None)
        environment["PATH"] = str(self.bin_dir) + os.pathsep + environment["PATH"]
        environment.update(overrides)
        return subprocess.run(
            [BASH, "-c", "set -e\n" + helper + "\n" + body],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_existing_license_values_are_preserved(self) -> None:
        result = self._run_helper(
            "resolve_verdi_license_environment\n"
            '[ "$LM_LICENSE_FILE" = existing-lm ]\n'
            '[ "$SNPSLMD_LICENSE_FILE" = existing-snps ]\n'
            "printf 'CONTRACT_PASS\\n'\n",
            VERDI_AUTO_LICENSE_IMPORT="0",
            LM_LICENSE_FILE="existing-lm",
            SNPSLMD_LICENSE_FILE="existing-snps",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONTRACT_PASS", result.stdout)
        self.assertNotIn("existing-lm", result.stdout + result.stderr)
        self.assertNotIn("existing-snps", result.stdout + result.stderr)

    def test_single_existing_license_value_is_mirrored(self) -> None:
        for source_name, target_name, value in (
            ("LM_LICENSE_FILE", "SNPSLMD_LICENSE_FILE", "lm-only"),
            ("SNPSLMD_LICENSE_FILE", "LM_LICENSE_FILE", "snps-only"),
        ):
            with self.subTest(source_name=source_name):
                result = self._run_helper(
                    "resolve_verdi_license_environment\n"
                    f'[ "${{{source_name}}}" = "{value}" ]\n'
                    f'[ "${{{target_name}}}" = "{value}" ]\n'
                    "printf 'CONTRACT_PASS\\n'\n",
                    VERDI_AUTO_LICENSE_IMPORT="0",
                    **{source_name: value},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONTRACT_PASS", result.stdout)
                self.assertNotIn(value, result.stdout + result.stderr)

    def test_root_imports_only_license_values_via_runuser(self) -> None:
        self._command(
            "id",
            """#!/usr/bin/env bash
case "${1-}" in
  -u) printf '0\\n' ;;
  -un) printf 'root\\n' ;;
  *) exit 1 ;;
esac
""",
        )
        self._command(
            "runuser",
            """#!/usr/bin/env bash
printf 'profile chatter that must stay captured\\n'
printf '__RSCHECK_LM_LICENSE_FILE__auto-lm\\n'
printf 'PATH=/untrusted/path\\n'
printf '__RSCHECK_SNPSLMD_LICENSE_FILE__auto-snps\\n'
""",
        )
        result = self._run_helper(
            "original_path=$PATH\n"
            "resolve_verdi_license_environment\n"
            '[ "$LM_LICENSE_FILE" = auto-lm ]\n'
            '[ "$SNPSLMD_LICENSE_FILE" = auto-snps ]\n'
            '[ "$PATH" = "$original_path" ]\n'
            "printf 'CONTRACT_PASS\\n'\n",
            GUI_SESSION_SELECTED_USER="desktop-user",
            VERDI_AUTO_LICENSE_IMPORT="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONTRACT_PASS", result.stdout)
        self.assertNotIn("auto-lm", result.stdout + result.stderr)
        self.assertNotIn("auto-snps", result.stdout + result.stderr)
        self.assertNotIn("profile chatter", result.stdout + result.stderr)

    def test_auto_import_can_be_disabled(self) -> None:
        marker = self.root / "runuser-called"
        self._command(
            "id",
            """#!/usr/bin/env bash
case "${1-}" in
  -u) printf '0\\n' ;;
  -un) printf 'root\\n' ;;
esac
""",
        )
        self._command(
            "runuser",
            """#!/usr/bin/env bash
touch "$FAKE_RUNUSER_MARKER"
exit 1
""",
        )
        result = self._run_helper(
            "resolve_verdi_license_environment\n"
            '[ -z "${LM_LICENSE_FILE:-}" ]\n'
            '[ -z "${SNPSLMD_LICENSE_FILE:-}" ]\n'
            '[ ! -e "$FAKE_RUNUSER_MARKER" ]\n'
            "printf 'CONTRACT_PASS\\n'\n",
            GUI_SESSION_SELECTED_USER="desktop-user",
            VERDI_AUTO_LICENSE_IMPORT="0",
            FAKE_RUNUSER_MARKER=str(marker),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONTRACT_PASS", result.stdout)

    def test_auto_import_failure_continues_without_values(self) -> None:
        self._command(
            "id",
            """#!/usr/bin/env bash
case "${1-}" in
  -u) printf '0\\n' ;;
  -un) printf 'root\\n' ;;
esac
""",
        )
        self._command("runuser", "#!/usr/bin/env bash\nexit 42\n")
        result = self._run_helper(
            "resolve_verdi_license_environment\n"
            '[ -z "${LM_LICENSE_FILE:-}" ]\n'
            '[ -z "${SNPSLMD_LICENSE_FILE:-}" ]\n'
            "printf 'CONTRACT_PASS\\n'\n",
            GUI_SESSION_SELECTED_USER="desktop-user",
            VERDI_AUTO_LICENSE_IMPORT="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONTRACT_PASS", result.stdout)
        self.assertIn("continuing with Verdi native diagnostics", result.stderr)


@unittest.skipUnless(
    BASH and sys.platform.startswith("linux"),
    "Linux bash is required for GUI session probe tests",
)
class GuiSessionProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.proc_dir = self.root / "proc"
        self.bin_dir.mkdir()
        self.proc_dir.mkdir()

        self._command(
            "xdpyinfo",
            """#!/usr/bin/env bash
case " ${FAKE_GOOD_DISPLAYS:-} " in
  *" ${DISPLAY:-<unset>} "*) ;;
  *) exit 1 ;;
esac
if [ "${FAKE_REQUIRE_XAUTHORITY_UNSET:-0}" = 1 ] && [ "${XAUTHORITY+x}" = x ]; then
  exit 1
fi
if [ -n "${FAKE_EXPECT_XAUTHORITY:-}" ] && [ "${XAUTHORITY:-}" != "$FAKE_EXPECT_XAUTHORITY" ]; then
  exit 1
fi
exit 0
""",
        )
        self._command(
            "pgrep",
            """#!/usr/bin/env bash
if [ -n "${FAKE_PGREP_PIDS:-}" ]; then
  printf '%b\n' "$FAKE_PGREP_PIDS"
  exit 0
fi
exit 1
""",
        )
        self._command(
            "ps",
            """#!/usr/bin/env bash
process_id=""
previous=""
for argument in "$@"; do
  if [ "$previous" = -p ]; then process_id=$argument; fi
  previous=$argument
done
if [ -n "$process_id" ] && [ -f "$FAKE_PROC_ROOT/$process_id/owner" ]; then
  sed -n '1p' "$FAKE_PROC_ROOT/$process_id/owner"
else
  printf '%s\n' "${FAKE_PS_USER:-desktop-user}"
fi
""",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _command(self, name: str, content: str) -> None:
        path = self.bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _process(
        self, process_id: int, *, owner: str = "desktop-user", **environment: str
    ) -> None:
        directory = self.proc_dir / str(process_id)
        directory.mkdir()
        payload = b"\0".join(
            f"{name}={value}".encode("utf-8") for name, value in environment.items()
        )
        (directory / "environ").write_bytes(payload + b"\0")
        (directory / "owner").write_text(owner, encoding="utf-8")

    def _run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "DISPLAY",
            "XAUTHORITY",
            "DBUS_SESSION_BUS_ADDRESS",
            "XDG_RUNTIME_DIR",
            "GUI_USER",
            "GUI_DISPLAY",
            "GUI_SESSION_PID",
            "GUI_XAUTHORITY",
            "GUI_DBUS_SESSION_BUS_ADDRESS",
            "GUI_XDG_RUNTIME_DIR",
            "LM_LICENSE_FILE",
            "SNPSLMD_LICENSE_FILE",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "PATH": str(self.bin_dir) + os.pathsep + environment["PATH"],
                "GUI_PROC_ROOT": str(self.proc_dir),
                "FAKE_PROC_ROOT": str(self.proc_dir),
                "GUI_PROBE_TIMEOUT": "1",
                "VERDI_HOME": "/does/not/exist",
            }
        )
        environment.update(overrides)
        return subprocess.run(
            [BASH, str(SCRIPT), "--gui-probe-only"],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_inherited_display_does_not_require_gnome_session(self) -> None:
        result = self._run(DISPLAY=":77", FAKE_GOOD_DISPLAYS=":77")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=current environment", result.stdout)
        self.assertIn("GUI probe PASS", result.stdout)

    def test_explicit_xauthority_is_used_with_inherited_display(self) -> None:
        xauthority = str(self.root / "explicit.auth")
        result = self._run(
            DISPLAY=":77",
            GUI_XAUTHORITY=xauthority,
            FAKE_GOOD_DISPLAYS=":77",
            FAKE_EXPECT_XAUTHORITY=xauthority,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=current environment", result.stdout)

    def test_explicit_cross_user_display_does_not_require_proc_access(self) -> None:
        xauthority = str(self.root / "cross-user.auth")
        result = self._run(
            GUI_USER="alice",
            GUI_DISPLAY=":78",
            GUI_XAUTHORITY=xauthority,
            FAKE_GOOD_DISPLAYS=":78",
            FAKE_EXPECT_XAUTHORITY=xauthority,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=explicit GUI_DISPLAY", result.stdout)
        self.assertIn("user=alice", result.stdout)

    def test_explicit_xauthority_overrides_process_environment(self) -> None:
        expected = str(self.root / "override.auth")
        self._process(302, DISPLAY=":79", XAUTHORITY="/stale/auth")
        result = self._run(
            GUI_SESSION_PID="302",
            GUI_XAUTHORITY=expected,
            FAKE_GOOD_DISPLAYS=":79",
            FAKE_EXPECT_XAUTHORITY=expected,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=process 302", result.stdout)

    def test_non_gnome_process_display_is_discovered(self) -> None:
        self._process(300, DISPLAY=":88")
        result = self._run(FAKE_GOOD_DISPLAYS=":88")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=process 300", result.stdout)
        self.assertIn("DISPLAY=:88", result.stdout)

    def test_unix_display_from_process_is_discovered(self) -> None:
        self._process(301, DISPLAY="unix:0")
        result = self._run(FAKE_GOOD_DISPLAYS="unix:0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=process 301", result.stdout)
        self.assertIn("DISPLAY=unix:0", result.stdout)

    def test_unreachable_candidate_is_skipped(self) -> None:
        self._process(100, DISPLAY=":dead")
        self._process(200, DISPLAY=":42")
        result = self._run(FAKE_GOOD_DISPLAYS=":42")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=process 200", result.stdout)

    def test_ssh_forwarded_display_without_xauthority(self) -> None:
        self._process(300, DISPLAY=":0")
        result = self._run(
            DISPLAY="localhost:10.0",
            FAKE_GOOD_DISPLAYS="localhost:10.0 :0",
            FAKE_REQUIRE_XAUTHORITY_UNSET="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=current environment", result.stdout)
        self.assertIn("DISPLAY=localhost:10.0", result.stdout)

    def test_gui_user_filters_process_owners(self) -> None:
        self._process(100, owner="alice", DISPLAY=":1")
        self._process(200, owner="bob", DISPLAY=":2")
        result = self._run(
            GUI_USER="bob",
            FAKE_GOOD_DISPLAYS=":1 :2",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("user=bob", result.stdout)
        self.assertIn("DISPLAY=:2", result.stdout)

    def test_wayland_session_uses_xwayland_and_xauthority(self) -> None:
        xauthority = str(self.root / "xwayland.auth")
        self._process(
            400,
            DISPLAY=":9",
            WAYLAND_DISPLAY="wayland-0",
            XAUTHORITY=xauthority,
            XDG_RUNTIME_DIR="/run/user/1000",
        )
        result = self._run(
            FAKE_GOOD_DISPLAYS=":9",
            FAKE_EXPECT_XAUTHORITY=xauthority,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DISPLAY=:9", result.stdout)

        no_xwayland = self.root / "proc-no-xwayland"
        no_xwayland.mkdir()
        original_proc = self.proc_dir
        self.proc_dir = no_xwayland
        try:
            self._process(500, WAYLAND_DISPLAY="wayland-0")
            failed = self._run(FAKE_GOOD_DISPLAYS=":9")
        finally:
            self.proc_dir = original_proc
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("Wayland sessions require a working Xwayland DISPLAY", failed.stderr)

    def test_duplicate_processes_on_one_display_are_not_ambiguous(self) -> None:
        self._process(100, DISPLAY=":0")
        self._process(200, DISPLAY=":0")
        result = self._run(FAKE_GOOD_DISPLAYS=":0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("multiple usable X11 displays", result.stderr)

    def test_explicit_session_pid_overrides_inherited_display(self) -> None:
        self._process(123, DISPLAY=":55", DBUS_SESSION_BUS_ADDRESS="unix:path=/run/fake")
        result = self._run(
            DISPLAY=":77",
            GUI_SESSION_PID="123",
            FAKE_GOOD_DISPLAYS=":55 :77",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=process 123", result.stdout)
        self.assertIn("DISPLAY=:55", result.stdout)

    def test_multiple_displays_require_explicit_selection(self) -> None:
        self._process(100, DISPLAY=":1")
        self._process(200, DISPLAY=":2")
        result = self._run(FAKE_GOOD_DISPLAYS=":1 :2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("multiple usable X11 displays", result.stderr)
        self.assertIn("GUI_DISPLAY", result.stderr)

        selected = self._run(GUI_DISPLAY=":2", FAKE_GOOD_DISPLAYS=":1 :2")
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertIn("DISPLAY=:2", selected.stdout)

    def test_no_usable_display_has_actionable_error(self) -> None:
        self._process(100, DISPLAY=":dead")
        result = self._run(FAKE_GOOD_DISPLAYS=":other")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no usable X11 display", result.stderr)
        self.assertIn("ssh -Y", result.stderr)
        self.assertIn("GUI_USER / GUI_DISPLAY / GUI_SESSION_PID", result.stderr)


if __name__ == "__main__":
    unittest.main()
