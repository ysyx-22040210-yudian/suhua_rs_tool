from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "test_vm_fresh_checkout.sh"
VERDI_SCRIPT = PROJECT_ROOT / "scripts" / "test_vm_verdi_gui.sh"
KDEBUG_PRESSURE_SCRIPT = PROJECT_ROOT / "scripts" / "test_kdebug_backend_pressure.sh"
SITE_ENV_RUNNER = PROJECT_ROOT / "scripts" / "lib" / "run_with_env_file.sh"
BASH = shutil.which("bash")


class VmFreshCheckoutStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_clone_contract_is_bounded_and_preserves_distinct_attempts(self) -> None:
        self.assertIn("MAX_CLONE_ATTEMPTS=3", self.source)
        self.assertIn('repo_attempt${attempt}', self.source)
        self.assertRegex(
            self.source,
            r'timeout\s+--kill-after=10\s+"\$CLONE_TIMEOUT"\s+'
            r'\\?\s*git\s+clone\s+'
            r'"\$REPOSITORY_URL"\s+"\$ATTEMPT_CHECKOUT"',
        )
        self.assertNotRegex(self.source, r"(?m)^\s*rm(?:\s|$)")
        self.assertNotIn("git -C", self.source)

    def test_commit_and_artifact_contract_is_explicit(self) -> None:
        self.assertIn('DEFAULT_REVISION="origin/main"', self.source)
        self.assertIn('git rev-parse --verify "${TARGET_REVISION}^{commit}"', self.source)
        self.assertIn('HEAD_COMMIT="$(git rev-parse HEAD)"', self.source)
        self.assertIn('[ "$HEAD_COMMIT" = "$RESOLVED_COMMIT" ]', self.source)
        self.assertIn('OUTPUT_BASE="$ARTIFACT_ROOT"', self.source)
        self.assertIn('bash "$REPO_ROOT/scripts/test_vm_verdi_gui.sh"', self.source)
        self.assertIn("full_vm_test.log", self.source)
        self.assertIn('VM_RUN_BASE="${VM_RUN_BASE:-${HOME:-}}"', self.source)
        self.assertIn('Collector backend: ${RSCHECK_COLLECTOR_BACKEND:-kdebug}', self.source)
        self.assertNotIn('RSCHECK_COLLECTOR_BACKEND:-npi', self.source)

    def test_site_environment_is_not_sourced_by_clone_controller(self) -> None:
        self.assertNotIn('source "$VERDI_ENV_FILE"', self.source)
        self.assertIn("VERDI_ENV_FILE must be an absolute path", self.source)
        self.assertIn("{ set +x; } 2>/dev/null", self.source)
        verdi_source = VERDI_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'RSCHECK_FIXED_PROJECT_ROOT="${PROJECT_ROOT:-', verdi_source
        )
        self.assertIn(
            'RSCHECK_FIXED_OUTPUT_BASE="${OUTPUT_BASE:-', verdi_source
        )
        self.assertIn(
            'RSCHECK_FIXED_COLLECTOR_BACKEND="${RSCHECK_COLLECTOR_BACKEND:-kdebug}"',
            verdi_source,
        )
        self.assertIn(
            '"RSCHECK_COLLECTOR_BACKEND=$RSCHECK_FIXED_COLLECTOR_BACKEND"',
            verdi_source,
        )
        self.assertIn('RSCHECK_FIXED_KDEBUG_BIN="${KDEBUG_BIN-}"', verdi_source)
        self.assertIn('"KDEBUG_BIN=$RSCHECK_FIXED_KDEBUG_BIN"', verdi_source)
        self.assertIn(
            '"KVERIF_EXPECTED_COMMIT=$RSCHECK_FIXED_KVERIF_EXPECTED_COMMIT"',
            verdi_source,
        )
        self.assertIn(
            '"KDEBUG_EXPECTED_SHA256=$RSCHECK_FIXED_KDEBUG_EXPECTED_SHA256"',
            verdi_source,
        )
        self.assertNotIn('"LM_LICENSE_FILE=$LM_LICENSE_FILE"', verdi_source)

    def test_verdi_driver_supports_npi_and_kdebug_collectors(self) -> None:
        verdi_source = VERDI_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'RSCHECK_COLLECTOR_BACKEND="${RSCHECK_COLLECTOR_BACKEND:-kdebug}"',
            verdi_source,
        )
        self.assertIn('npi|kdebug)', verdi_source)
        self.assertIn(
            'COLLECTOR="$PROJECT_ROOT/scripts/rs_kdebug_collector.py"',
            verdi_source,
        )
        self.assertIn('export KDEBUG_BIN', verdi_source)
        self.assertIn(
            'export RSCHECK_COLLECTOR_TIMEOUT_SECONDS="$NPI_TIMEOUT"', verdi_source
        )
        self.assertIn('ldd "$KDEBUG_BIN"', verdi_source)
        self.assertIn("kdebug frontend must have no direct NPI dependency", verdi_source)
        self.assertIn('KDEBUG_MANIFEST="$TEST_ROOT/kdebug_build_manifest.txt"', verdi_source)
        self.assertIn('export RSCHECK_KDEBUG_HOME="$TEST_ROOT/kdebug_home"', verdi_source)
        self.assertIn('export KDEBUG_HOME="$RSCHECK_KDEBUG_HOME"', verdi_source)
        self.assertIn('export PYTHON="$(command -v "$PYTHON_BIN")"', verdi_source)
        self.assertIn('assert_no_new_kdebug_processes', verdi_source)
        self.assertIn('timeout --signal=TERM --kill-after=2 "$NPI_TIMEOUT"', verdi_source)
        self.assertEqual(verdi_source.count('run_collector "$'), 2)
        self.assertIn("kdebug isolated logs PASS", verdi_source)
        self.assertIn("rscheck_commit=%s", verdi_source)
        self.assertIn("kverif_commit=%s", verdi_source)
        self.assertIn('NPI_CLI_ARGS=(--npi-lib-dir "$NPI_LIB_DIR")', verdi_source)
        self.assertIn('"${NPI_CLI_ARGS[@]}"', verdi_source)
        self.assertIn("Collector backend: direct C++ NPI baseline", verdi_source)
        self.assertIn(
            "Collector backend: kdebug JSON action rscheck.inventory", verdi_source
        )
        self.assertIn('echo "COLLECTOR_BACKEND=$RSCHECK_COLLECTOR_BACKEND"', verdi_source)
        self.assertIn('collector_command=("$PYTHON_BIN" "$COLLECTOR")', verdi_source)
        self.assertIn(
            "kverif checkout must be clean when KVERIF_EXPECTED_COMMIT is set",
            verdi_source,
        )
        self.assertIn("status --porcelain --untracked-files=normal", verdi_source)

    def test_kdebug_mode_does_not_require_baseline_npi_build_inputs(self) -> None:
        verdi_source = VERDI_SCRIPT.read_text(encoding="utf-8")
        conditional_start = verdi_source.index(
            'if [ "$RSCHECK_COLLECTOR_BACKEND" = npi ]; then',
            verdi_source.index('NPI_CLI_ARGS=()'),
        )
        conditional_end = verdi_source.index(
            '\n[ -d "$PROJECT_ROOT" ]', conditional_start
        )
        npi_setup = verdi_source[conditional_start:conditional_end]
        for marker in (
            'command -v make',
            'command -v "$CXX"',
            'npi.h',
            'npi_L1.h',
            'libNPI.so',
            'libnpiL1.so',
            'export LD_LIBRARY_PATH',
        ):
            self.assertIn(marker, npi_setup)
        prefix = verdi_source[:conditional_start]
        self.assertNotIn('command -v make', prefix)
        self.assertNotIn('command -v "$CXX"', prefix)

    def test_kdebug_pressure_driver_is_bounded_and_elab_only(self) -> None:
        pressure_source = KDEBUG_PRESSURE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('PRESSURE_ITERATIONS="${PRESSURE_ITERATIONS:-20}"', pressure_source)
        self.assertIn('ACTION_TIMEOUT_SECONDS="${ACTION_TIMEOUT_SECONDS:-60}"', pressure_source)
        self.assertIn("rscheck.inventory", pressure_source)
        self.assertIn('"elab_db": sys.argv[1]', pressure_source)
        self.assertIn("timeout --signal=TERM --kill-after=3", pressure_source)
        self.assertIn("RSCHECK_COLLECTOR_TIMEOUT_SECONDS=1", pressure_source)
        self.assertIn("KDEBUG_TIMEOUT", pressure_source)
        self.assertIn("INTERNAL_ENGINE_FAILED", pressure_source)
        self.assertIn('kill -TERM "$CANCEL_PID"', pressure_source)
        self.assertIn("verdi_pids_for_tmpdir", pressure_source)
        self.assertIn("KDEBUG_TCL_RESPONSE_JSON=", pressure_source)
        self.assertIn("did not reach a live Verdi NPI action", pressure_source)
        self.assertIn("frontend cancellation left", pressure_source)
        self.assertIn('root.rglob("crash_marker*")', pressure_source)
        self.assertNotIn("--filelist", pressure_source)
        self.assertNotIn("--source", pressure_source)
        self.assertNotIn("work.lib++/", pressure_source)

    def test_script_contains_no_host_or_secret_literals(self) -> None:
        self.assertIsNone(
            re.search(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])", self.source)
        )
        self.assertIsNone(re.search(r"https?://[^/\s:@]+:[^/\s@]+@", self.source))
        self.assertIsNone(
            re.search(
                r"(?im)^\s*(?:password|token|lm_license_file|snpslmd_license_file)\s*=\s*\S+",
                self.source,
            )
        )


@unittest.skipUnless(
    BASH and os.name != "nt" and sys.platform != "win32",
    "a native non-Windows bash is required for argument tests",
)
class VmFreshCheckoutArgumentTests(unittest.TestCase):
    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        return subprocess.run(
            [BASH, str(SCRIPT), *arguments],
            cwd=PROJECT_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_bash_syntax(self) -> None:
        assert BASH is not None
        for script in (SCRIPT, VERDI_SCRIPT, KDEBUG_PRESSURE_SCRIPT, SITE_ENV_RUNNER):
            completed = subprocess.run(
                [BASH, "-n", str(script)],
                cwd=PROJECT_ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_help_documents_commit(self) -> None:
        completed = self.run_script("--help")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--commit REV", completed.stdout)
        self.assertIn("origin/main", completed.stdout)
        self.assertIn("Required full kverif SHA", completed.stdout)
        self.assertIn("Required SHA-256", completed.stdout)

    def test_kdebug_fresh_run_requires_pinned_backend_identity(self) -> None:
        assert BASH is not None
        environment = os.environ.copy()
        environment["RSCHECK_COLLECTOR_BACKEND"] = "kdebug"
        environment.pop("KVERIF_EXPECTED_COMMIT", None)
        environment.pop("KDEBUG_EXPECTED_SHA256", None)
        completed = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("require KVERIF_EXPECTED_COMMIT", completed.stderr)

    def test_unknown_option_returns_usage_error(self) -> None:
        completed = self.run_script("--unknown")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("unsupported argument", completed.stderr)

    def test_missing_commit_value_returns_usage_error(self) -> None:
        completed = self.run_script("--commit")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("requires a revision", completed.stderr)

    def test_positional_argument_returns_usage_error(self) -> None:
        completed = self.run_script("unexpected")
        self.assertEqual(completed.returncode, 2)

    def test_extra_argument_returns_usage_error(self) -> None:
        completed = self.run_script("--commit", "HEAD", "extra")
        self.assertEqual(completed.returncode, 2)

    def test_duplicate_commit_returns_usage_error(self) -> None:
        completed = self.run_script("--commit", "HEAD", "--commit", "HEAD~1")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("only once", completed.stderr)

    def test_relative_site_environment_file_fails_before_clone(self) -> None:
        assert BASH is not None
        environment = os.environ.copy()
        environment["VERDI_ENV_FILE"] = "relative-site-env.sh"
        completed = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("absolute path", completed.stderr)
        self.assertNotIn("Clone attempt", completed.stdout + completed.stderr)

    def test_relative_run_base_fails_before_clone(self) -> None:
        assert BASH is not None
        environment = os.environ.copy()
        environment["VM_RUN_BASE"] = "."
        completed = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("absolute path", completed.stderr)
        self.assertNotIn("Clone attempt", completed.stdout + completed.stderr)


@unittest.skipUnless(
    BASH and os.name != "nt" and sys.platform != "win32",
    "a native non-Windows bash is required for controller integration tests",
)
class VmFreshCheckoutControllerIntegrationTests(unittest.TestCase):
    COMMIT = "a" * 40

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.clone_counter = self.root / "clone-counter"
        self.child_result = self.root / "child-result.txt"

        self.fake_driver = self.root / "fake-test_vm_verdi_gui.sh"
        self.fake_driver.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            '[ "$PROJECT_ROOT" = "$(pwd -P)" ]\n'
            'case "$OUTPUT_BASE" in "$PROJECT_ROOT"/*) exit 31 ;; esac\n'
            "mkdir -p \"$OUTPUT_BASE\"\n"
            "printf 'project=%s\\noutput=%s\\npwd=%s\\n' "
            '"$PROJECT_ROOT" "$OUTPUT_BASE" "$(pwd -P)" '
            '>"$FAKE_CHILD_RESULT"\n'
            "printf 'fake-artifact\\n' >\"$OUTPUT_BASE/fake-artifact.txt\"\n"
            "printf 'FAKE_DRIVER_PASS\\n'\n",
            encoding="utf-8",
        )
        self.fake_driver.chmod(0o755)

        fake_git = self.bin_dir / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            'case "${1-}" in\n'
            "  clone)\n"
            '    count=0; [ ! -f "$FAKE_CLONE_COUNTER" ] || '
            'count=$(cat "$FAKE_CLONE_COUNTER")\n'
            '    count=$((count + 1)); printf "%s\\n" "$count" '
            '>"$FAKE_CLONE_COUNTER"\n'
            '    checkout=$3; mkdir -p "$checkout"\n'
            '    if [ "$count" -eq 1 ]; then printf "partial\\n" '
            '>"$checkout/partial-clone"; exit 17; fi\n'
            '    mkdir -p "$checkout/scripts"\n'
            '    cp "$FAKE_CLONED_DRIVER" '
            '"$checkout/scripts/test_vm_verdi_gui.sh"\n'
            "    ;;\n"
            "  rev-parse)\n"
            f"    printf '{self.COMMIT}\\n'\n"
            "    ;;\n"
            "  checkout)\n"
            "    exit 0\n"
            "    ;;\n"
            "  *)\n"
            '    printf "unexpected fake git args: %s\\n" "$*" >&2\n'
            "    exit 90\n"
            "    ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_retry_commit_pin_and_fixed_child_paths(self) -> None:
        assert BASH is not None
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": str(self.bin_dir) + os.pathsep + environment["PATH"],
                "VM_RUN_BASE": str(self.root),
                "CLONE_TIMEOUT": "10",
                "FAKE_CLONE_COUNTER": str(self.clone_counter),
                "FAKE_CLONED_DRIVER": str(self.fake_driver),
                "FAKE_CHILD_RESULT": str(self.child_result),
            }
        )
        completed = subprocess.run(
            [BASH, str(SCRIPT), "--commit", self.COMMIT],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Clone attempt 1 failed with exit code 17", completed.stdout)
        self.assertIn("Clone attempt 2 succeeded", completed.stdout)
        self.assertIn("Verified commit: " + self.COMMIT, completed.stdout)
        self.assertIn("FAKE_DRIVER_PASS", completed.stdout)
        self.assertEqual(self.clone_counter.read_text(encoding="utf-8").strip(), "2")

        run_root_match = re.search(r"^RUN_ROOT=(.+)$", completed.stdout, re.MULTILINE)
        self.assertIsNotNone(run_root_match)
        assert run_root_match is not None
        run_root = Path(run_root_match.group(1))
        self.assertTrue((run_root / "repo_attempt1" / "clone_status.txt").is_file())
        self.assertEqual(
            (run_root / "repo_attempt1" / "clone_status.txt")
            .read_text(encoding="utf-8")
            .strip(),
            "exit_code=17",
        )
        self.assertTrue((run_root / "repo_attempt1" / "repository" / "partial-clone").is_file())
        self.assertTrue((run_root / "full_vm_test.log").is_file())
        self.assertTrue((run_root / "artifacts" / "fake-artifact.txt").is_file())

        child_values = dict(
            line.split("=", 1)
            for line in self.child_result.read_text(encoding="utf-8").splitlines()
        )
        expected_repo = run_root / "repo_attempt2" / "repository"
        self.assertEqual(Path(child_values["project"]), expected_repo)
        self.assertEqual(Path(child_values["pwd"]), expected_repo)
        self.assertEqual(Path(child_values["output"]), run_root / "artifacts")


if __name__ == "__main__":
    unittest.main()
