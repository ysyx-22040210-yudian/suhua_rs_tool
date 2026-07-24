from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "launch_rscheck_gui.sh"
BASH = shutil.which("bash")


@unittest.skipUnless(
    BASH and sys.platform.startswith("linux"),
    "Linux bash is required for rscheck GUI launcher tests",
)
class RsCheckGuiLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bin_dir = Path(self.temp_dir.name) / "bin"
        self.bin_dir.mkdir()
        probe = self.bin_dir / "xdpyinfo"
        probe.write_text(
            "#!/usr/bin/env bash\n[ \"${DISPLAY:-}\" = \"${FAKE_DISPLAY:-}\" ]\n",
            encoding="utf-8",
        )
        probe.chmod(0o755)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _command(self, name: str, content: str) -> Path:
        path = self.bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run(
        self, *arguments: str, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": str(self.bin_dir) + os.pathsep + environment["PATH"],
                "DISPLAY": ":77",
                "FAKE_DISPLAY": ":77",
                "GUI_PROBE_TIMEOUT": "1",
            }
        )
        environment.update(overrides)
        return subprocess.run(
            [BASH, str(SCRIPT), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_probe_only_needs_no_tkinter_or_project_runtime(self) -> None:
        result = self._run("--probe-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rscheck GUI probe PASS", result.stdout)

    def test_unexpected_arguments_are_rejected(self) -> None:
        result = self._run("--filelist", "files.f")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected argument", result.stderr)

    def test_launcher_executes_only_python_gui_entrypoint(self) -> None:
        project = Path(self.temp_dir.name) / "project with spaces"
        project.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname='fake'\n", encoding="utf-8")
        arguments_file = Path(self.temp_dir.name) / "python.args"
        display_file = Path(self.temp_dir.name) / "python.display"
        python = self._command(
            "fake-python",
            """#!/usr/bin/env bash
if [ "${1:-}" = -c ]; then exit 0; fi
printf '%s\n' "$@" >"$FAKE_PYTHON_ARGS"
printf '%s\n' "${DISPLAY:-}" >"$FAKE_PYTHON_DISPLAY"
""",
        )
        result = self._run(
            PYTHON_BIN=str(python),
            PYTHON_ENABLE="",
            PROJECT_ROOT=str(project),
            FAKE_PYTHON_ARGS=str(arguments_file),
            FAKE_PYTHON_DISPLAY=str(display_file),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments_file.read_text("utf-8").splitlines(), ["-m", "rscheck", "gui"])
        self.assertEqual(display_file.read_text("utf-8").strip(), ":77")

    def test_missing_tkinter_has_actionable_error(self) -> None:
        python = self._command(
            "python-without-tk",
            "#!/usr/bin/env bash\nexit 1\n",
        )
        result = self._run(
            PYTHON_BIN=str(python),
            PYTHON_ENABLE="",
            PROJECT_ROOT=str(ROOT),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tkinter is unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
