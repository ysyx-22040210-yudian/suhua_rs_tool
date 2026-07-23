from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "launch_verdi_gui.sh"
BASH = shutil.which("bash")


@unittest.skipUnless(
    BASH and sys.platform.startswith("linux"),
    "Linux bash is required for Verdi GUI launcher tests",
)
class VerdiGuiLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.args_file = self.root / "verdi.args"
        self.kdb = self.root / "design with spaces" / "kdb.elab++"
        self.kdb.mkdir(parents=True)

        self._command(
            "xdpyinfo",
            """#!/usr/bin/env bash
[ "${DISPLAY:-}" = "${FAKE_GOOD_DISPLAY:-}" ]
""",
        )
        self.verdi = self._command(
            "fake-verdi",
            """#!/usr/bin/env bash
printf '%s\n' "$@" >"$FAKE_VERDI_ARGS"
""",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _command(self, name: str, content: str) -> Path:
        path = self.bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run(self, *arguments: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "XAUTHORITY",
            "DBUS_SESSION_BUS_ADDRESS",
            "XDG_RUNTIME_DIR",
            "LM_LICENSE_FILE",
            "SNPSLMD_LICENSE_FILE",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "PATH": str(self.bin_dir) + os.pathsep + environment["PATH"],
                "DISPLAY": ":77",
                "FAKE_GOOD_DISPLAY": ":77",
                "FAKE_VERDI_ARGS": str(self.args_file),
                "VERDI_BIN": str(self.verdi),
                "GUI_PROBE_TIMEOUT": "1",
            }
        )
        environment.update(overrides)
        return subprocess.run(
            [BASH, str(LAUNCHER), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_launch_passes_only_elab_and_exact_kdb_path(self) -> None:
        result = self._run("--elab-db", str(self.kdb))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.args_file.read_text(encoding="utf-8").splitlines(),
            ["-elab", str(self.kdb.resolve())],
        )

    def test_probe_needs_no_verdi_license_or_kdb(self) -> None:
        result = self._run("--probe-only", VERDI_BIN="/does/not/exist")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GUI probe PASS", result.stdout)

    def test_invalid_design_inputs_and_passthrough_are_rejected(self) -> None:
        ordinary_file = self.root / "not-a-kdb"
        ordinary_file.write_text("not a KDB", encoding="utf-8")
        work_library = self.root / "work.lib++"
        work_library.mkdir()
        work_library_alias = self.root / "work-library-alias"
        work_library_alias.symlink_to(work_library, target_is_directory=True)

        cases = (
            ("missing", ("--elab-db", str(self.root / "missing"))),
            ("file", ("--elab-db", str(ordinary_file))),
            ("worklib", ("--elab-db", str(work_library))),
            ("worklib-extra-slashes", ("--elab-db", str(work_library) + "//")),
            ("worklib-symlink", ("--elab-db", str(work_library_alias))),
            ("filelist", ("--elab-db", str(self.kdb), "-f", "files.f")),
        )
        for name, arguments in cases:
            with self.subTest(name=name):
                result = self._run(*arguments)
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.args_file.exists())


if __name__ == "__main__":
    unittest.main()
