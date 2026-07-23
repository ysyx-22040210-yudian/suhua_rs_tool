#!/usr/bin/env bash

# Portable launcher for an existing Verdi elaborated KDB. This command never
# accepts RTL, a filelist, or arbitrary Verdi argument passthrough.
set -Ee -o pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
VERDI_BIN="${VERDI_BIN-}"
VERDI_HOME="${VERDI_HOME:-${NOVAS_INST_DIR:-}}"
BACKGROUND=0
PROBE_ONLY=0
ELAB_DB=""

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/gui_session.sh"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
Usage:
  bash scripts/launch_verdi_gui.sh --probe-only
  bash scripts/launch_verdi_gui.sh --elab-db DIR [--background]

Only an existing Verdi elaborated KDB directory is accepted.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --probe-only)
      PROBE_ONLY=1
      shift
      ;;
    --elab-db)
      [ "$#" -ge 2 ] || { usage; fail "--elab-db requires a directory"; }
      [ -z "$ELAB_DB" ] || fail "--elab-db may only be specified once"
      ELAB_DB=$2
      shift 2
      ;;
    --background)
      BACKGROUND=1
      shift
      ;;
    --|-f|-F|-sv|-verilog|-vhdl|-lib|-path|-top)
      fail "RTL, filelist, top, library, and argument passthrough are not supported"
      ;;
    -*)
      usage
      fail "unsupported option: $1"
      ;;
    *)
      usage
      fail "unexpected positional argument: $1"
      ;;
  esac
done

if [ "$PROBE_ONLY" -eq 1 ] && { [ -n "$ELAB_DB" ] || [ "$BACKGROUND" -eq 1 ]; }; then
  fail "--probe-only cannot be combined with launch options"
fi

if ! gui_session_resolve; then
  exit 1
fi
echo "GUI access OK: source=$GUI_SESSION_SOURCE user=$GUI_SESSION_SELECTED_USER DISPLAY=$DISPLAY"

if [ "$PROBE_ONLY" -eq 1 ]; then
  echo "GUI probe PASS"
  exit 0
fi

[ -n "$ELAB_DB" ] || { usage; fail "--elab-db is required"; }
[ -d "$ELAB_DB" ] || fail "elaborated KDB is not a directory: $ELAB_DB"
ELAB_DB_INPUT="$ELAB_DB"
ELAB_DB="$(cd "$ELAB_DB" 2>/dev/null && pwd -P)" ||
  fail "cannot resolve elaborated KDB directory: $ELAB_DB_INPUT"
case "$ELAB_DB" in
  */work.lib++|work.lib++) fail "work.lib++ is a compile library, not an elaborated KDB" ;;
esac

if [ -z "$VERDI_BIN" ]; then
  if [ -n "$VERDI_HOME" ] && [ -x "$VERDI_HOME/bin/verdi" ]; then
    VERDI_BIN="$VERDI_HOME/bin/verdi"
  elif command -v verdi >/dev/null 2>&1; then
    VERDI_BIN="$(command -v verdi)"
  elif [ -x /home/synopsys/verdi/Verdi_O-2018.09-SP2/bin/verdi ]; then
    VERDI_BIN=/home/synopsys/verdi/Verdi_O-2018.09-SP2/bin/verdi
  else
    fail "Verdi not found; set VERDI_BIN, VERDI_HOME, NOVAS_INST_DIR, or PATH"
  fi
fi
[ -x "$VERDI_BIN" ] || fail "Verdi executable is not accessible: $VERDI_BIN"

echo "Launching Verdi with elaborated KDB: $ELAB_DB"
if [ "$BACKGROUND" -eq 0 ]; then
  exec "$VERDI_BIN" -elab "$ELAB_DB"
fi

VERDI_GUI_LOG="${VERDI_GUI_LOG:-${TMPDIR:-/tmp}/verdi_gui_${USER:-user}_$$.log}"
nohup "$VERDI_BIN" -elab "$ELAB_DB" >"$VERDI_GUI_LOG" 2>&1 </dev/null &
VERDI_GUI_PID=$!
disown "$VERDI_GUI_PID" 2>/dev/null || true
echo "Verdi PID=$VERDI_GUI_PID"
echo "Verdi log=$VERDI_GUI_LOG"
case "$DISPLAY" in
  localhost:*|127.0.0.1:*)
    echo "Keep the SSH X11 forwarding connection open while using this GUI."
    ;;
esac
