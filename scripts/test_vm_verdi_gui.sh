#!/usr/bin/env bash

# End-to-end test for the sample RTL: Python tests, NPI build, fresh KDB,
# Verdi GUI launch, and an online check against that same elaborated KDB.
set -Ee -o pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
VERDI_HOME="${VERDI_HOME:-/home/synopsys/verdi/Verdi_O-2018.09-SP2}"
NPI_PLATFORM="${NPI_PLATFORM:-LINUX64}"
GUI_USER="${GUI_USER:-host}"
GUI_SESSION_PATTERN="${GUI_SESSION_PATTERN:-gnome-session-binary}"
GUI_START_TIMEOUT="${GUI_START_TIMEOUT:-60}"
NPI_TIMEOUT="${NPI_TIMEOUT:-180}"
OUTPUT_BASE="${OUTPUT_BASE:-$PROJECT_ROOT/output}"
PYTHON_ENABLE="${PYTHON_ENABLE-/opt/rh/rh-python38/enable}"
GCC_ENABLE="${GCC_ENABLE-/opt/rh/devtoolset-11/enable}"

TEST_ROOT=""

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

case "$GUI_START_TIMEOUT" in
  ''|*[!0-9]*) fail "GUI_START_TIMEOUT must be a positive integer" ;;
esac
[ "$GUI_START_TIMEOUT" -gt 0 ] || fail "GUI_START_TIMEOUT must be greater than zero"

for command_name in pgrep tr sed xdpyinfo xwininfo make ldd mktemp nohup sort comm tee; do
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

GUI_PID="$(pgrep -u "$GUI_USER" -o -f "$GUI_SESSION_PATTERN" || true)"
[ -n "$GUI_PID" ] || fail "no active $GUI_SESSION_PATTERN session for user $GUI_USER"
[ -r "/proc/$GUI_PID/environ" ] || fail "cannot read GUI environment from PID $GUI_PID"

read_gui_env() {
  local variable_name=$1
  tr '\0' '\n' <"/proc/$GUI_PID/environ" |
    sed -n "s/^${variable_name}=//p" |
    sed -n '1p'
}

export DISPLAY="$(read_gui_env DISPLAY)"
export XAUTHORITY="$(read_gui_env XAUTHORITY)"
export DBUS_SESSION_BUS_ADDRESS="$(read_gui_env DBUS_SESSION_BUS_ADDRESS)"
XDG_RUNTIME_DIR="$(read_gui_env XDG_RUNTIME_DIR)"
[ -z "$XDG_RUNTIME_DIR" ] || export XDG_RUNTIME_DIR

[ -n "$DISPLAY" ] || fail "DISPLAY is missing from the GUI session"
[ -n "$XAUTHORITY" ] || fail "XAUTHORITY is missing from the GUI session"
[ -n "$DBUS_SESSION_BUS_ADDRESS" ] || fail "DBUS_SESSION_BUS_ADDRESS is missing from the GUI session"
[ -r "$XAUTHORITY" ] || fail "XAUTHORITY is not readable: $XAUTHORITY"
xdpyinfo >/dev/null
echo "GUI access OK: user=$GUI_USER DISPLAY=$DISPLAY"

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
echo "Verdi remains open on DISPLAY=$DISPLAY for manual hierarchy inspection."
