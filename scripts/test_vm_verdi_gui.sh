#!/usr/bin/env bash

# End-to-end test for the sample RTL: Python tests, NPI build, fresh KDB,
# Verdi GUI launch, and an online check against that same elaborated KDB.
set -Ee -o pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
VERDI_HOME="${VERDI_HOME:-/home/synopsys/verdi/Verdi_O-2018.09-SP2}"
NPI_PLATFORM="${NPI_PLATFORM:-LINUX64}"
GUI_USER="${GUI_USER-}"
GUI_DISPLAY="${GUI_DISPLAY-}"
GUI_SESSION_PID="${GUI_SESSION_PID-}"
GUI_SESSION_PATTERN="${GUI_SESSION_PATTERN:-gnome-session-binary|gnome-session|gnome-shell|startplasma|plasmashell|ksmserver|kwin_x11|kwin_wayland|xfce4-session|mate-session|cinnamon|lxsession|lxqt-session|Xwayland}"
GUI_PROC_ROOT="${GUI_PROC_ROOT:-/proc}"
GUI_PROBE_TIMEOUT="${GUI_PROBE_TIMEOUT:-5}"
GUI_START_TIMEOUT="${GUI_START_TIMEOUT:-60}"
NPI_TIMEOUT="${NPI_TIMEOUT:-180}"
OUTPUT_BASE="${OUTPUT_BASE:-$PROJECT_ROOT/output}"
PYTHON_ENABLE="${PYTHON_ENABLE-/opt/rh/rh-python38/enable}"
GCC_ENABLE="${GCC_ENABLE-/opt/rh/devtoolset-11/enable}"

TEST_ROOT=""
GUI_PROBE_ONLY=0

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

on_error() {
  local rc=$?
  echo "FAILED: line $1, exit $rc" >&2
  if [ -n "$TEST_ROOT" ]; then
    echo "Test artifacts retained at: $TEST_ROOT" >&2
  fi
  exit "$rc"
}

trap 'on_error $LINENO' ERR

usage() {
  echo "Usage: bash scripts/test_vm_verdi_gui.sh [--gui-probe-only]" >&2
}

case "${1-}" in
  "") ;;
  --gui-probe-only) GUI_PROBE_ONLY=1 ;;
  *) usage; fail "unexpected argument: $1" ;;
esac
[ "$#" -le 1 ] || { usage; fail "only one optional argument is supported"; }

for numeric_setting in "$GUI_PROBE_TIMEOUT" "$GUI_START_TIMEOUT"; do
  case "$numeric_setting" in
    ''|*[!0-9]*) fail "GUI_PROBE_TIMEOUT and GUI_START_TIMEOUT must be positive integers" ;;
  esac
  [ "$numeric_setting" -gt 0 ] || fail "GUI timeouts must be greater than zero"
done

for command_name in sed xdpyinfo timeout; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required GUI probe command not found: $command_name"
done

read_process_env() {
  local process_id=$1
  local variable_name=$2
  local entry

  while IFS= read -r -d '' entry; do
    case "$entry" in
      "$variable_name="*)
        printf '%s\n' "${entry#*=}"
        return 0
        ;;
    esac
  done <"$GUI_PROC_ROOT/$process_id/environ" 2>/dev/null
  return 1
}

set_optional_gui_env() {
  local variable_name=$1
  local variable_value=$2
  if [ -n "$variable_value" ]; then
    export "$variable_name=$variable_value"
  else
    unset "$variable_name"
  fi
}

probe_process_gui() {
  local process_id=$1
  local process_owner
  local candidate_display
  local candidate_xauthority
  local candidate_dbus
  local candidate_runtime
  local candidate_home

  [ -r "$GUI_PROC_ROOT/$process_id/environ" ] || return 1
  candidate_display="$(read_process_env "$process_id" DISPLAY || true)"
  [ -n "$candidate_display" ] || return 1
  if [ -n "$GUI_DISPLAY" ] && [ "$candidate_display" != "$GUI_DISPLAY" ]; then
    return 1
  fi
  if [ -z "$GUI_USER" ] && [ -z "$GUI_DISPLAY" ] && [ -z "$GUI_SESSION_PID" ]; then
    case "$candidate_display" in
      :[0-9]*|unix/:[0-9]*) ;;
      *) return 1 ;;
    esac
  fi

  process_owner="$(ps -o user:64= -p "$process_id" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true)"
  [ -n "$process_owner" ] || process_owner=unknown
  if [ -n "$GUI_USER" ] && [ "$process_owner" != "$GUI_USER" ]; then
    return 1
  fi
  if [ -z "$GUI_USER" ]; then
    case "$process_owner" in
      gdm|sddm|lightdm) return 1 ;;
    esac
  fi

  candidate_xauthority="$(read_process_env "$process_id" XAUTHORITY || true)"
  candidate_dbus="$(read_process_env "$process_id" DBUS_SESSION_BUS_ADDRESS || true)"
  candidate_runtime="$(read_process_env "$process_id" XDG_RUNTIME_DIR || true)"
  candidate_home="$(read_process_env "$process_id" HOME || true)"
  if [ -z "$candidate_xauthority" ] && [ -n "$candidate_home" ] && [ -r "$candidate_home/.Xauthority" ]; then
    candidate_xauthority="$candidate_home/.Xauthority"
  fi

  if ! (
    export DISPLAY="$candidate_display"
    set_optional_gui_env XAUTHORITY "$candidate_xauthority"
    timeout "$GUI_PROBE_TIMEOUT" xdpyinfo >/dev/null 2>&1
  ); then
    return 1
  fi

  CANDIDATE_PID="$process_id"
  CANDIDATE_USER="$process_owner"
  CANDIDATE_DISPLAY="$candidate_display"
  CANDIDATE_XAUTHORITY="$candidate_xauthority"
  CANDIDATE_DBUS="$candidate_dbus"
  CANDIDATE_RUNTIME="$candidate_runtime"
  return 0
}

record_gui_candidate() {
  local process_id=$1
  local session_key
  local quick_display

  case " $SEEN_GUI_PIDS " in
    *" $process_id "*) return ;;
  esac
  SEEN_GUI_PIDS="$SEEN_GUI_PIDS $process_id"

  quick_display="$(read_process_env "$process_id" DISPLAY || true)"
  if [ -n "$quick_display" ]; then
    session_key="|$quick_display|"
    case "$FOUND_GUI_KEYS" in
      *"$session_key"*) return ;;
    esac
  fi

  probe_process_gui "$process_id" || return 0
  session_key="|$CANDIDATE_DISPLAY|"
  case "$FOUND_GUI_KEYS" in
    *"$session_key"*) return ;;
  esac

  FOUND_GUI_KEYS="$FOUND_GUI_KEYS$session_key"
  AVAILABLE_GUI_SESSIONS="${AVAILABLE_GUI_SESSIONS}${CANDIDATE_USER} DISPLAY=${CANDIDATE_DISPLAY} PID=${CANDIDATE_PID}\n"
  if [ -z "$FOUND_GUI_DISPLAY" ]; then
    FOUND_GUI_PID="$CANDIDATE_PID"
    FOUND_GUI_USER="$CANDIDATE_USER"
    FOUND_GUI_DISPLAY="$CANDIDATE_DISPLAY"
    FOUND_GUI_XAUTHORITY="$CANDIDATE_XAUTHORITY"
    FOUND_GUI_DBUS="$CANDIDATE_DBUS"
    FOUND_GUI_RUNTIME="$CANDIDATE_RUNTIME"
  else
    GUI_AMBIGUOUS=1
  fi
}

resolve_gui_environment() {
  local initial_display="${DISPLAY:-}"
  local current_user="${USER:-${LOGNAME:-current-user}}"
  local candidate_pids=""
  local process_dir
  local process_id

  if [ -z "$GUI_SESSION_PID" ] &&
     { [ -z "$GUI_USER" ] || [ "$GUI_USER" = "$current_user" ]; } &&
     { [ -z "$GUI_DISPLAY" ] || [ "$GUI_DISPLAY" = "$initial_display" ]; } &&
     [ -n "$initial_display" ] && timeout "$GUI_PROBE_TIMEOUT" xdpyinfo >/dev/null 2>&1; then
    GUI_SOURCE="current environment"
    GUI_SELECTED_USER="$current_user"
    return 0
  fi

  command -v ps >/dev/null 2>&1 || fail "ps is required when the current DISPLAY is unusable"
  if [ -z "$GUI_SESSION_PID" ]; then
    command -v pgrep >/dev/null 2>&1 || fail "pgrep is required for automatic GUI discovery"
  fi

  SEEN_GUI_PIDS=""
  FOUND_GUI_KEYS=""
  AVAILABLE_GUI_SESSIONS=""
  FOUND_GUI_PID=""
  FOUND_GUI_USER=""
  FOUND_GUI_DISPLAY=""
  FOUND_GUI_XAUTHORITY=""
  FOUND_GUI_DBUS=""
  FOUND_GUI_RUNTIME=""
  GUI_AMBIGUOUS=0

  if [ -n "$GUI_SESSION_PID" ]; then
    case "$GUI_SESSION_PID" in
      *[!0-9]*|'') fail "GUI_SESSION_PID must be a numeric process ID" ;;
    esac
    record_gui_candidate "$GUI_SESSION_PID"
  else
    if [ -n "$GUI_USER" ]; then
      candidate_pids="$(pgrep -u "$GUI_USER" -f "$GUI_SESSION_PATTERN" 2>/dev/null || true)"
    else
      candidate_pids="$(pgrep -f "$GUI_SESSION_PATTERN" 2>/dev/null || true)"
    fi
    for process_id in $candidate_pids; do
      record_gui_candidate "$process_id"
    done

    # Fall back to every readable process environment. This covers VNC,
    # Openbox, SSH-created X servers, and desktop implementations not listed
    # in GUI_SESSION_PATTERN.
    for process_dir in "$GUI_PROC_ROOT"/[0-9]*; do
      [ -d "$process_dir" ] || continue
      process_id="${process_dir##*/}"
      record_gui_candidate "$process_id"
    done
  fi

  if [ "$GUI_AMBIGUOUS" -ne 0 ] && [ -z "$GUI_DISPLAY" ]; then
    echo "ERROR: multiple usable X11 displays were found:" >&2
    printf '%b' "$AVAILABLE_GUI_SESSIONS" >&2
    echo "Set GUI_DISPLAY to the intended DISPLAY, or run from that graphical/SSH-X shell." >&2
    return 1
  fi

  if [ -z "$FOUND_GUI_DISPLAY" ]; then
    echo "ERROR: no usable X11 display was found." >&2
    echo "Current DISPLAY=${initial_display:-<unset>} did not pass xdpyinfo." >&2
    echo "Run 'bash scripts/test_vm_verdi_gui.sh --gui-probe-only' from a graphical terminal," >&2
    echo "connect with 'ssh -Y', or set GUI_USER / GUI_DISPLAY / GUI_SESSION_PID explicitly." >&2
    echo "Wayland sessions require a working Xwayland DISPLAY for this Verdi release." >&2
    [ -z "$GUI_USER" ] || echo "GUI_USER filter: $GUI_USER" >&2
    return 1
  fi

  export DISPLAY="$FOUND_GUI_DISPLAY"
  set_optional_gui_env XAUTHORITY "$FOUND_GUI_XAUTHORITY"
  set_optional_gui_env DBUS_SESSION_BUS_ADDRESS "$FOUND_GUI_DBUS"
  set_optional_gui_env XDG_RUNTIME_DIR "$FOUND_GUI_RUNTIME"
  GUI_SOURCE="process $FOUND_GUI_PID"
  GUI_SELECTED_USER="$FOUND_GUI_USER"
  return 0
}

if ! resolve_gui_environment; then
  exit 1
fi

echo "GUI access OK: source=$GUI_SOURCE user=$GUI_SELECTED_USER DISPLAY=$DISPLAY"
if [ "$GUI_PROBE_ONLY" -eq 1 ]; then
  echo "GUI probe PASS"
  exit 0
fi

for command_name in xwininfo make ldd mktemp nohup sort comm tee; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command not found: $command_name"
done

[ -d "$PROJECT_ROOT" ] || fail "PROJECT_ROOT is not a directory: $PROJECT_ROOT"
[ -f "$PROJECT_ROOT/pyproject.toml" ] || fail "not an rtl-rs-check repository: $PROJECT_ROOT"
[ -x "$VERDI_HOME/bin/vericom" ] || fail "vericom is not executable under VERDI_HOME"
[ -x "$VERDI_HOME/bin/elabcom" ] || fail "elabcom is not executable under VERDI_HOME"
[ -x "$VERDI_HOME/bin/verdi" ] || fail "verdi is not executable under VERDI_HOME"
[ -f "$VERDI_HOME/share/NPI/inc/npi.h" ] || fail "NPI header not found under VERDI_HOME"
[ -f "$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM/libNPI.so" ] || fail "libNPI.so not found for $NPI_PLATFORM"

if [ -z "${LM_LICENSE_FILE:-}" ] && [ -z "${SNPSLMD_LICENSE_FILE:-}" ]; then
  fail "set LM_LICENSE_FILE or SNPSLMD_LICENSE_FILE before running this script"
fi
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-$SNPSLMD_LICENSE_FILE}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"

if [ -n "$PYTHON_ENABLE" ]; then
  [ -f "$PYTHON_ENABLE" ] || fail "Python enable script not found: $PYTHON_ENABLE"
  # shellcheck disable=SC1090
  source "$PYTHON_ENABLE"
fi
if [ -n "$GCC_ENABLE" ]; then
  [ -f "$GCC_ENABLE" ] || fail "GCC enable script not found: $GCC_ENABLE"
  # shellcheck disable=SC1090
  source "$GCC_ENABLE"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is not available"
command -v g++ >/dev/null 2>&1 || fail "g++ is not available"
export VERDI_HOME NPI_PLATFORM
export NOVAS_INST_DIR="$VERDI_HOME"
export PYTHONDONTWRITEBYTECODE=1

mkdir -p "$OUTPUT_BASE"
TEST_ROOT="$(mktemp -d "$OUTPUT_BASE/verdi_gui_test.XXXXXXXX")"

cd "$PROJECT_ROOT"
python3 --version
g++ --version
python3 -m unittest discover -v 2>&1 | tee "$TEST_ROOT/python_tests.log"
grep -Eq '^Ran [0-9]+ tests? in ' "$TEST_ROOT/python_tests.log"
grep -q '^OK$' "$TEST_ROOT/python_tests.log"

NPI_BUILD_DIR="$TEST_ROOT/npi_build"
make -C npi \
  BUILD_DIR="$NPI_BUILD_DIR" \
  VERDI_HOME="$VERDI_HOME" \
  NPI_PLATFORM="$NPI_PLATFORM" \
  CXX=g++
COLLECTOR="$NPI_BUILD_DIR/rs_npi_collector"
[ -x "$COLLECTOR" ] || fail "collector was not built: $COLLECTOR"
ldd "$COLLECTOR" | tee "$TEST_ROOT/collector_ldd.txt" | grep 'libNPI.so'
if grep -q 'not found' "$TEST_ROOT/collector_ldd.txt"; then
  fail "collector has unresolved shared libraries"
fi

ELAB_ROOT="$TEST_ROOT/example_elab"
ELAB_DB="$ELAB_ROOT/kdb.elab++"
mkdir -p "$ELAB_ROOT"

cd "$ELAB_ROOT"
"$VERDI_HOME/bin/vericom" -sv "$PROJECT_ROOT/examples/rtl/rs_example.sv"
[ -d "$ELAB_ROOT/work.lib++" ] || fail "vericom did not create work.lib++"

"$VERDI_HOME/bin/elabcom" -top top -elab "$ELAB_DB"
[ -d "$ELAB_DB" ] || fail "elabcom did not create the elaborated KDB"

VERDI_LOG="$TEST_ROOT/verdi_gui.log"
BASELINE_WINDOWS="$TEST_ROOT/verdi_windows.before.txt"
CURRENT_WINDOWS="$TEST_ROOT/verdi_windows.current.txt"
NEW_WINDOWS="$TEST_ROOT/verdi_windows.new.txt"
BASELINE_PIDS="$TEST_ROOT/verdi_pids.before.txt"
CURRENT_PIDS="$TEST_ROOT/verdi_pids.current.txt"
NEW_PIDS="$TEST_ROOT/verdi_pids.new.txt"

xwininfo -root -tree 2>/dev/null |
  grep -Ei 'verdi|novas|debussy' |
  sort >"$BASELINE_WINDOWS" || true
pgrep -f '[v]erdi|[N]ovas|[d]ebussy' |
  sort -n >"$BASELINE_PIDS" || true

nohup "$VERDI_HOME/bin/verdi" -elab "$ELAB_DB" >"$VERDI_LOG" 2>&1 </dev/null &
VERDI_LAUNCH_PID=$!
disown "$VERDI_LAUNCH_PID" 2>/dev/null || true
echo "Verdi launch PID=$VERDI_LAUNCH_PID"

elapsed=0
VERDI_WINDOWS=""
VERDI_READY=0
while [ "$elapsed" -lt "$GUI_START_TIMEOUT" ]; do
  xwininfo -root -tree 2>/dev/null |
    grep -Ei 'verdi|novas|debussy' |
    sort >"$CURRENT_WINDOWS" || true
  comm -13 "$BASELINE_WINDOWS" "$CURRENT_WINDOWS" >"$NEW_WINDOWS"
  VERDI_WINDOWS="$(sed -n '1,$p' "$NEW_WINDOWS")"
  if grep -Eq '<Verdi:nTraceMain[^>]*>[[:space:]]+top([[:space:]]|$)' "$NEW_WINDOWS"; then
    VERDI_READY=1
    break
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done

pgrep -f '[v]erdi|[N]ovas|[d]ebussy' |
  sort -n >"$CURRENT_PIDS" || true
comm -13 "$BASELINE_PIDS" "$CURRENT_PIDS" >"$NEW_PIDS"
VERDI_PROCESSES="$(pgrep -af '[v]erdi|[N]ovas|[d]ebussy' || true)"

if [ "$VERDI_READY" -ne 1 ]; then
  echo "Verdi-related processes:" >&2
  printf '%s\n' "$VERDI_PROCESSES" >&2
  echo "New Verdi-related windows:" >&2
  printf '%s\n' "$VERDI_WINDOWS" >&2
  echo "Verdi log:" >&2
  sed -n '1,200p' "$VERDI_LOG" >&2
  fail "no new Verdi X11 window loaded elaborated top 'top' within ${GUI_START_TIMEOUT}s"
fi

echo "Verdi GUI loaded elaborated top 'top' after ${elapsed}s:"
printf '%s\n' "$VERDI_WINDOWS"

cd "$PROJECT_ROOT"
POS_INVENTORY="$TEST_ROOT/positive_inventory.json"
POS_REPORT="$TEST_ROOT/positive_report.json"
POS_CSV="$TEST_ROOT/positive_report.csv"

# The checker receives only the elaborated KDB as its design input. RTL and
# filelist arguments are intentionally not accepted by this command.
python3 -m rscheck check \
  --excel "$PROJECT_ROOT/examples/specs.csv" \
  --config "$PROJECT_ROOT/config/rscheck.example.json" \
  --sheet 1 \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-timeout "$NPI_TIMEOUT" \
  --keep-inventory "$POS_INVENTORY" \
  --json-report "$POS_REPORT" \
  --csv-report "$POS_CSV"

[ -s "$POS_INVENTORY" ] || fail "positive inventory was not written"
[ -s "$POS_REPORT" ] || fail "positive JSON report was not written"
[ -s "$POS_CSV" ] || fail "positive CSV report was not written"

python3 - "$POS_REPORT" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    summary = json.load(stream)["summary"]

expected = {
    "passed": True,
    "rows": 2,
    "passed_rows": 2,
    "failed_rows": 0,
    "errors": 0,
    "warnings": 0,
}
if summary != expected:
    raise SystemExit("unexpected positive summary: {!r}".format(summary))
print("positive summary OK:", summary)
PY

trap - ERR
echo "PASS: Verdi GUI launch and online NPI check completed."
echo "ELAB_DB=$ELAB_DB"
echo "REPORT=$POS_REPORT"
echo "VERDI_LOG=$VERDI_LOG"
case "$DISPLAY" in
  localhost:*|127.0.0.1:*)
    echo "Verdi uses SSH X11 forwarding on DISPLAY=$DISPLAY; keep this SSH connection open."
    ;;
  *)
    echo "Verdi remains open on DISPLAY=$DISPLAY for manual hierarchy inspection."
    ;;
esac
