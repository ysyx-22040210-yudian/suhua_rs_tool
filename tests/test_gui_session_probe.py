from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test_vm_verdi_gui.sh"
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash is required for GUI session probe tests")
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
  *" ${DISPLAY:-<unset>} "*) exit 0 ;;
  *) exit 1 ;;
esac
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
printf '%s\n' "${FAKE_PS_USER:-desktop-user}"
""",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _command(self, name: str, content: str) -> None:
        path = self.bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _process(self, process_id: int, **environment: str) -> None:
        directory = self.proc_dir / str(process_id)
        directory.mkdir()
        payload = b"\0".join(
            f"{name}={value}".encode("utf-8") for name, value in environment.items()
        )
        (directory / "environ").write_bytes(payload + b"\0")

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
            "LM_LICENSE_FILE",
            "SNPSLMD_LICENSE_FILE",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "PATH": str(self.bin_dir) + os.pathsep + environment["PATH"],
                "GUI_PROC_ROOT": str(self.proc_dir),
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

    def test_non_gnome_process_display_is_discovered(self) -> None:
        self._process(300, DISPLAY=":88")
        result = self._run(FAKE_GOOD_DISPLAYS=":88")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=process 300", result.stdout)
        self.assertIn("DISPLAY=:88", result.stdout)

    def test_unreachable_candidate_is_skipped(self) -> None:
        self._process(100, DISPLAY=":dead")
        self._process(200, DISPLAY=":42")
        result = self._run(FAKE_GOOD_DISPLAYS=":42")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=process 200", result.stdout)

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
