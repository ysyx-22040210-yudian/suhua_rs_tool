#!/usr/bin/env bash

# Launch the rscheck desktop GUI through the shared X11/Xwayland resolver.
set -Ee -o pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHON_ENABLE_IS_SET="${PYTHON_ENABLE+x}"
PYTHON_ENABLE="${PYTHON_ENABLE-}"
PROBE_ONLY=0

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/gui_session.sh"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

usage() {
  echo "Usage: bash scripts/launch_rscheck_gui.sh [--probe-only]" >&2
}

case "${1-}" in
  "") ;;
  --probe-only) PROBE_ONLY=1 ;;
  *) usage; fail "unexpected argument: $1" ;;
esac
[ "$#" -le 1 ] || { usage; fail "only one optional argument is supported"; }

if ! gui_session_resolve; then
  exit 1
fi
echo "GUI access OK: source=$GUI_SESSION_SOURCE user=$GUI_SESSION_SELECTED_USER DISPLAY=$DISPLAY"
if [ "$PROBE_ONLY" -eq 1 ]; then
  echo "rscheck GUI probe PASS"
  exit 0
fi

if [ -z "$PYTHON_ENABLE_IS_SET" ] && [ -f /opt/rh/rh-python38/enable ]; then
  PYTHON_ENABLE=/opt/rh/rh-python38/enable
fi
if [ -n "$PYTHON_ENABLE" ]; then
  [ -f "$PYTHON_ENABLE" ] || fail "Python enable script not found: $PYTHON_ENABLE"
  # shellcheck disable=SC1090
  source "$PYTHON_ENABLE"
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python executable not found: $PYTHON_BIN"
[ -f "$PROJECT_ROOT/pyproject.toml" ] || fail "not an rtl-rs-check repository: $PROJECT_ROOT"
if ! "$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1; then
  fail "tkinter is unavailable; install python3-tk (Debian/Ubuntu) or the matching python3-tkinter package (RHEL/CentOS)"
fi

cd "$PROJECT_ROOT"
export PYTHONDONTWRITEBYTECODE=1
exec "$PYTHON_BIN" -m rscheck gui
