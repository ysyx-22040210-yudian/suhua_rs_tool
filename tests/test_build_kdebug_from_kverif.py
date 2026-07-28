from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "build_kdebug_from_kverif.sh"
SOURCE = SCRIPT.read_text(encoding="utf-8")
NATIVE_BASH = None if os.name == "nt" else shutil.which("bash")
INTEGRATION_MIRROR = os.environ.get("KDEBUG_BUILD_INTEGRATION_MIRROR", "").strip()
INTEGRATION_ELAB_DB = os.environ.get("KDEBUG_BUILD_INTEGRATION_ELAB_DB", "").strip()
INTEGRATION_POSITION = os.environ.get("KDEBUG_BUILD_INTEGRATION_POSITION", "").strip()


class BuildKdebugFromKverifTests(unittest.TestCase):
    def test_script_locks_the_repository_branch_and_commit_contract(self) -> None:
        self.assertIn(
            'readonly KVERIF_REPO_URL="https://github.com/ysyx-22040210-yudian/kverif.git"',
            SOURCE,
        )
        self.assertIn('readonly KVERIF_BRANCH="codex/rscheck-elab-inventory"', SOURCE)
        self.assertIn(
            'readonly KVERIF_COMMIT="2b43b799c8f7f8586a9e6c2128335e74d971e633"',
            SOURCE,
        )
        self.assertNotIn('KVERIF_COMMIT="${KVERIF_COMMIT:-', SOURCE)
        self.assertNotIn('KVERIF_BRANCH="${KVERIF_BRANCH:-', SOURCE)
        self.assertIn("--single-branch", SOURCE)
        self.assertIn("--depth 1", SOURCE)

    def test_script_builds_tests_and_validates_the_compiled_elf(self) -> None:
        self.assertIn("import jsonschema, pytest", SOURCE)
        self.assertIn("Python test dependencies are missing", SOURCE)
        self.assertIn('make -C "$KDEBUG_DIR" clean', SOURCE)
        self.assertIn('make -C "$KDEBUG_DIR" -j"$BUILD_JOBS" all', SOURCE)
        self.assertIn('make -C "$KDEBUG_DIR" test-fast', SOURCE)
        self.assertIn("KDEBUG_MAGIC=", SOURCE)
        self.assertIn("7f454c46", SOURCE)
        self.assertIn("libNPI|libnpiL1|not found", SOURCE)
        self.assertIn("rscheck.inventory", SOURCE)

    def test_script_packages_and_rechecks_the_compiled_runtime(self) -> None:
        self.assertIn('cp "$KDEBUG_BIN" "$PACKAGE_DIR/kdebug"', SOURCE)
        self.assertIn('cp -a "$KDEBUG_DIR/libexec" "$PACKAGE_DIR/libexec"', SOURCE)
        self.assertIn('cp "$SOURCE_ROOT/LICENSE" "$PACKAGE_DIR/LICENSE"', SOURCE)
        self.assertIn("KDEBUG_INTERNAL_NPI_TCL", SOURCE)
        self.assertIn("private runtime resource", SOURCE)
        self.assertIn('sha256sum -c SHA256SUMS', SOURCE)
        self.assertIn('"$RUNTIME_KDEBUG_BIN" actions', SOURCE)

    def test_script_creates_a_deterministic_portable_archive(self) -> None:
        self.assertIn('find "$PACKAGE_NAME" -print0', SOURCE)
        self.assertIn("sort -z", SOURCE)
        self.assertIn("--null", SOURCE)
        self.assertIn("--no-recursion", SOURCE)
        self.assertIn('--mtime="@$SOURCE_DATE_EPOCH"', SOURCE)
        self.assertIn("--numeric-owner", SOURCE)
        self.assertIn("unset GZIP", SOURCE)
        self.assertIn("gzip -n -6", SOURCE)
        self.assertIn("BUILD_INFO.txt", SOURCE)
        self.assertIn("artifact_checksums.sha256", SOURCE)

    def test_manifest_uses_relative_paths_and_does_not_record_mirror_url(self) -> None:
        manifest_block = SOURCE.split('MANIFEST="$RUN_ROOT/build_manifest.txt"', 1)[1]
        self.assertIn("kverif_clone_source=trusted_mirror", manifest_block)
        self.assertIn("source_root=./kverif", manifest_block)
        self.assertIn("runtime_dir=./%s", manifest_block)
        self.assertNotIn(
            "printf 'kverif_clone_source=%s\\n' \"$KVERIF_MIRROR_URL\"",
            manifest_block,
        )
        self.assertNotIn("printf 'source_root=%s", manifest_block)

    def test_script_supports_a_real_elaborated_kdb_smoke(self) -> None:
        self.assertIn("--smoke-elab-db", SOURCE)
        self.assertIn("--smoke-position", SOURCE)
        self.assertIn('"$RUNTIME_KDEBUG_BIN" --json -', SOURCE)
        self.assertIn('PYTHON="$PYTHON_BIN"', SOURCE)
        self.assertIn("timeout --signal=TERM --kill-after=5s", SOURCE)
        self.assertIn("LIVE_SMOKE=PASS schema=v3", SOURCE)
        self.assertIn('position.get("found") is not True', SOURCE)
        self.assertIn('position.get("instances")', SOURCE)
        self.assertIn("kdebug response error[%s]", SOURCE)

    def test_script_documents_the_only_rscheck_exec_command(self) -> None:
        self.assertIn("RSCHECK_EXEC=", SOURCE)
        self.assertIn("/kdebug --json -", SOURCE)
        self.assertIn("rscheck never executes", SOURCE)

    def test_script_has_no_vm_or_secret_literal(self) -> None:
        lowered = SOURCE.lower()
        self.assertIsNone(re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", SOURCE))
        self.assertIsNone(
            re.search(
                r"(?im)^\s*(?:password|passwd|token)\s*=\s*\S+",
                SOURCE,
            )
        )
        self.assertNotIn("lm_license_file=", lowered)
        self.assertNotIn("snpslmd_license_file=", lowered)

    @unittest.skipIf(NATIVE_BASH is None, "native Linux bash is required")
    def test_bash_syntax_help_and_paired_smoke_arguments(self) -> None:
        assert NATIVE_BASH is not None
        subprocess.run([NATIVE_BASH, "-n", str(SCRIPT)], check=True, cwd=PROJECT_ROOT)
        completed = subprocess.run(
            [NATIVE_BASH, str(SCRIPT), "--help"],
            check=False,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("<runtime-dir>/kdebug --json -", completed.stdout)
        self.assertIn("cannot be overridden", completed.stdout)

        missing_position = subprocess.run(
            [
                NATIVE_BASH,
                str(SCRIPT),
                "--output-base",
                str(PROJECT_ROOT),
                "--smoke-elab-db",
                "/does/not/matter.elab++",
            ],
            check=False,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(missing_position.returncode, 1)
        self.assertIn("must be provided together", missing_position.stderr)

    @unittest.skipUnless(
        NATIVE_BASH is not None and INTEGRATION_MIRROR,
        "set KDEBUG_BUILD_INTEGRATION_MIRROR on Linux for the full build test",
    )
    def test_full_clone_build_package_and_optional_live_smoke(self) -> None:
        assert NATIVE_BASH is not None
        with tempfile.TemporaryDirectory(prefix="kdebug-build-integration-") as name:
            command = [
                NATIVE_BASH,
                str(SCRIPT),
                "--output-base",
                name,
                "--python",
                sys.executable,
                "--jobs",
                "2",
            ]
            if INTEGRATION_ELAB_DB or INTEGRATION_POSITION:
                self.assertTrue(INTEGRATION_ELAB_DB and INTEGRATION_POSITION)
                command.extend(
                    [
                        "--smoke-elab-db",
                        INTEGRATION_ELAB_DB,
                        "--smoke-position",
                        INTEGRATION_POSITION,
                    ]
                )
            environment = os.environ.copy()
            environment["KVERIF_MIRROR_URL"] = INTEGRATION_MIRROR
            completed = subprocess.run(
                command,
                check=False,
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1800,
            )
            diagnostic = completed.stdout + "\n" + completed.stderr
            if completed.returncode != 0:
                for log_path in sorted(Path(name).glob("**/*.log")):
                    try:
                        diagnostic += (
                            f"\n--- {log_path.name} ---\n"
                            + log_path.read_text(encoding="utf-8", errors="replace")
                        )
                    except OSError as exc:
                        diagnostic += f"\nCannot read {log_path}: {exc}\n"
            self.assertEqual(completed.returncode, 0, diagnostic)
            values = {}
            for line in completed.stdout.splitlines():
                key, separator, value = line.partition("=")
                if separator and key in {
                    "KVERIF_COMMIT",
                    "RUNTIME_KDEBUG_BIN",
                    "RUNTIME_ARCHIVE",
                    "RUNTIME_CHECKSUMS",
                    "ARTIFACT_CHECKSUMS",
                }:
                    values[key] = value
            self.assertEqual(
                values.get("KVERIF_COMMIT"),
                "2b43b799c8f7f8586a9e6c2128335e74d971e633",
            )
            for key in (
                "RUNTIME_KDEBUG_BIN",
                "RUNTIME_ARCHIVE",
                "RUNTIME_CHECKSUMS",
                "ARTIFACT_CHECKSUMS",
            ):
                self.assertTrue(Path(values[key]).is_file(), key)
            self.assertEqual(
                Path(values["RUNTIME_KDEBUG_BIN"]).read_bytes()[:4], b"\x7fELF"
            )
            self.assertIn("--json -", completed.stdout)
            if INTEGRATION_ELAB_DB:
                self.assertIn("LIVE_SMOKE=PASS schema=v3", completed.stdout)


if __name__ == "__main__":
    unittest.main()
