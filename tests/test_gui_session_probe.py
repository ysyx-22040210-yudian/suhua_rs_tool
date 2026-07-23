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
BASH = shutil.which("bash")


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
