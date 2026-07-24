#!/usr/bin/env bash

# End-to-end test for the sample RTL: Python tests, NPI build, fresh KDB,
# Verdi GUI launch, and an online check against that same elaborated KDB.
set -Ee -o pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
VERDI_HOME="${VERDI_HOME:-${NOVAS_INST_DIR:-}}"
VERDI_BIN="${VERDI_BIN-}"
VERICOM_BIN="${VERICOM_BIN-}"
ELABCOM_BIN="${ELABCOM_BIN-}"
NPI_PLATFORM="${NPI_PLATFORM-}"
NPI_INC_DIR="${NPI_INC_DIR-}"
NPI_LIB_DIR="${NPI_LIB_DIR-}"
GUI_START_TIMEOUT="${GUI_START_TIMEOUT:-180}"
VERDI_GENERIC_READY_DELAY="${VERDI_GENERIC_READY_DELAY:-10}"
VERDI_WINDOW_REGEX="${VERDI_WINDOW_REGEX:-verdi|novas|debussy}"
VERDI_READY_REGEX="${VERDI_READY_REGEX:-<Verdi:nTraceMain[^>]*>[[:space:]]+top([[:space:]]|$)}"
NPI_TIMEOUT="${NPI_TIMEOUT:-180}"
OUTPUT_BASE="${OUTPUT_BASE:-$PROJECT_ROOT/output}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CXX="${CXX:-g++}"
PYTHON_ENABLE_IS_SET="${PYTHON_ENABLE+x}"
GCC_ENABLE_IS_SET="${GCC_ENABLE+x}"
PYTHON_ENABLE="${PYTHON_ENABLE-}"
GCC_ENABLE="${GCC_ENABLE-}"
KEEP_VERDI_GUI="${KEEP_VERDI_GUI:-0}"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/gui_session.sh"

TEST_ROOT=""
GUI_PROBE_ONLY=0
VERDI_LAUNCH_PID=""

cleanup() {
  local rc=$?
  trap - EXIT
  if [ "$KEEP_VERDI_GUI" = 0 ] && [ -n "$VERDI_LAUNCH_PID" ] &&
     kill -0 "$VERDI_LAUNCH_PID" 2>/dev/null; then
    kill "$VERDI_LAUNCH_PID" 2>/dev/null || true
    for _ in {1..30}; do
      kill -0 "$VERDI_LAUNCH_PID" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$VERDI_LAUNCH_PID" 2>/dev/null; then
      kill -KILL "$VERDI_LAUNCH_PID" 2>/dev/null || true
    fi
    wait "$VERDI_LAUNCH_PID" 2>/dev/null || true
  fi
  exit "$rc"
}

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

trap cleanup EXIT
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

case "$KEEP_VERDI_GUI" in
  0|1) ;;
  *) fail "KEEP_VERDI_GUI must be 0 or 1" ;;
esac

for numeric_setting in "$GUI_START_TIMEOUT" "$VERDI_GENERIC_READY_DELAY"; do
  case "$numeric_setting" in
    ''|*[!0-9]*) fail "GUI_START_TIMEOUT and VERDI_GENERIC_READY_DELAY must be non-negative integers" ;;
  esac
done
[ "$GUI_START_TIMEOUT" -gt 0 ] || fail "GUI_START_TIMEOUT must be greater than zero"

if ! gui_session_resolve; then
  exit 1
fi

echo "GUI access OK: source=$GUI_SESSION_SOURCE user=$GUI_SESSION_SELECTED_USER DISPLAY=$DISPLAY"
if [ "$GUI_PROBE_ONLY" -eq 1 ]; then
  echo "GUI probe PASS"
  exit 0
fi

for command_name in xwininfo make ldd mktemp nohup sort comm tee grep; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    if [ "$command_name" = xwininfo ]; then
      fail "xwininfo not found; install x11-utils (Debian/Ubuntu) or xorg-x11-utils (RHEL/CentOS)"
    fi
    fail "required command not found: $command_name"
  fi
done

if [ -z "$PYTHON_ENABLE_IS_SET" ] && [ -f /opt/rh/rh-python38/enable ]; then
  PYTHON_ENABLE=/opt/rh/rh-python38/enable
fi
if [ -z "$GCC_ENABLE_IS_SET" ] && [ -f /opt/rh/devtoolset-11/enable ]; then
  GCC_ENABLE=/opt/rh/devtoolset-11/enable
fi
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

command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python executable not found: $PYTHON_BIN"
command -v "$CXX" >/dev/null 2>&1 || fail "C++ compiler not found: $CXX"

if [ -z "$VERDI_BIN" ]; then
  if [ -n "$VERDI_HOME" ] && [ -x "$VERDI_HOME/bin/verdi" ]; then
    VERDI_BIN="$VERDI_HOME/bin/verdi"
  elif command -v verdi >/dev/null 2>&1; then
    VERDI_BIN="$(command -v verdi)"
  elif [ -x /home/synopsys/verdi/Verdi_O-2018.09-SP2/bin/verdi ]; then
    VERDI_HOME=/home/synopsys/verdi/Verdi_O-2018.09-SP2
    VERDI_BIN="$VERDI_HOME/bin/verdi"
  else
    fail "Verdi not found; set VERDI_HOME, NOVAS_INST_DIR, or VERDI_BIN"
  fi
fi
[ -x "$VERDI_BIN" ] || fail "Verdi executable is not accessible: $VERDI_BIN"

if [ -z "$VERDI_HOME" ]; then
  VERDI_BIN_DIR="$(cd "$(dirname "$VERDI_BIN")" && pwd -P)"
  VERDI_HOME="$(cd "$VERDI_BIN_DIR/.." && pwd -P)"
fi
VERICOM_BIN="${VERICOM_BIN:-$VERDI_HOME/bin/vericom}"
ELABCOM_BIN="${ELABCOM_BIN:-$VERDI_HOME/bin/elabcom}"
[ -x "$VERICOM_BIN" ] || fail "vericom not found; set VERICOM_BIN or correct VERDI_HOME"
[ -x "$ELABCOM_BIN" ] || fail "elabcom not found; set ELABCOM_BIN or correct VERDI_HOME"

NPI_INC_DIR="${NPI_INC_DIR:-$VERDI_HOME/share/NPI/inc}"
if [ -z "$NPI_LIB_DIR" ]; then
  if [ -n "$NPI_PLATFORM" ] && [ -f "$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM/libNPI.so" ]; then
    NPI_LIB_DIR="$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM"
  elif [ -f "$VERDI_HOME/share/NPI/lib/LINUX64/libNPI.so" ]; then
    NPI_PLATFORM=LINUX64
    NPI_LIB_DIR="$VERDI_HOME/share/NPI/lib/LINUX64"
  else
    for candidate_library in "$VERDI_HOME"/share/NPI/lib/*/libNPI.so; do
      [ -f "$candidate_library" ] || continue
      NPI_LIB_DIR="${candidate_library%/libNPI.so}"
      NPI_PLATFORM="${NPI_LIB_DIR##*/}"
      break
    done
  fi
fi
[ -n "$NPI_PLATFORM" ] || NPI_PLATFORM="${NPI_LIB_DIR##*/}"
[ -f "$NPI_INC_DIR/npi.h" ] || fail "NPI header not found: $NPI_INC_DIR/npi.h"
[ -f "$NPI_LIB_DIR/libNPI.so" ] || fail "libNPI.so not found; set NPI_LIB_DIR"

[ -d "$PROJECT_ROOT" ] || fail "PROJECT_ROOT is not a directory: $PROJECT_ROOT"
[ -f "$PROJECT_ROOT/pyproject.toml" ] || fail "not an rtl-rs-check repository: $PROJECT_ROOT"

if [ -z "${LM_LICENSE_FILE:-}" ] && [ -z "${SNPSLMD_LICENSE_FILE:-}" ]; then
  fail "set LM_LICENSE_FILE or SNPSLMD_LICENSE_FILE before running this script"
fi
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-$SNPSLMD_LICENSE_FILE}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"
export VERDI_HOME NPI_PLATFORM
export NOVAS_INST_DIR="$VERDI_HOME"
export PYTHONDONTWRITEBYTECODE=1

mkdir -p "$OUTPUT_BASE"
TEST_ROOT="$(mktemp -d "$OUTPUT_BASE/verdi_gui_test.XXXXXXXX")"

cd "$PROJECT_ROOT"
"$PYTHON_BIN" --version
"$CXX" --version
"$PYTHON_BIN" -m unittest discover -v 2>&1 | tee "$TEST_ROOT/python_tests.log"
grep -Eq '^Ran [0-9]+ tests? in ' "$TEST_ROOT/python_tests.log"
grep -q '^OK' "$TEST_ROOT/python_tests.log"

NPI_BUILD_DIR="$TEST_ROOT/npi_build"
make -C npi \
  BUILD_DIR="$NPI_BUILD_DIR" \
  VERDI_HOME="$VERDI_HOME" \
  NPI_PLATFORM="$NPI_PLATFORM" \
  NPI_INC="$NPI_INC_DIR" \
  NPI_LIB="$NPI_LIB_DIR" \
  CXX="$CXX"
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
"$VERICOM_BIN" -sv "$PROJECT_ROOT/examples/rtl/rs_example.sv"
[ -d "$ELAB_ROOT/work.lib++" ] || fail "vericom did not create work.lib++"

"$ELABCOM_BIN" -top top -elab "$ELAB_DB"
[ -d "$ELAB_DB" ] || fail "elabcom did not create the elaborated KDB"

VERDI_LOG="$TEST_ROOT/verdi_gui.log"
BASELINE_WINDOWS="$TEST_ROOT/verdi_windows.before.txt"
CURRENT_WINDOWS="$TEST_ROOT/verdi_windows.current.txt"
NEW_WINDOWS="$TEST_ROOT/verdi_windows.new.txt"
BASELINE_PIDS="$TEST_ROOT/verdi_pids.before.txt"
CURRENT_PIDS="$TEST_ROOT/verdi_pids.current.txt"
NEW_PIDS="$TEST_ROOT/verdi_pids.new.txt"

xwininfo -root -tree 2>/dev/null |
  grep -Ei "$VERDI_WINDOW_REGEX" |
  LC_ALL=C sort >"$BASELINE_WINDOWS" || true
pgrep -f '[v]erdi|[N]ovas|[d]ebussy' |
  LC_ALL=C sort >"$BASELINE_PIDS" || true

nohup "$VERDI_BIN" -elab "$ELAB_DB" >"$VERDI_LOG" 2>&1 </dev/null &
VERDI_LAUNCH_PID=$!
echo "Verdi launch PID=$VERDI_LAUNCH_PID"

verdi_launch_uses_elab_db() {
  local argument
  local previous=""
  [ -r "/proc/$VERDI_LAUNCH_PID/cmdline" ] || return 1
  while IFS= read -r -d '' argument; do
    if [ "$previous" = "-elab" ] && [ "$argument" = "$ELAB_DB" ]; then
      return 0
    fi
    previous="$argument"
  done <"/proc/$VERDI_LAUNCH_PID/cmdline"
  return 1
}

elapsed=0
VERDI_WINDOWS=""
VERDI_READY=0
VERDI_TITLE_CONFIRMED=0
while [ "$elapsed" -lt "$GUI_START_TIMEOUT" ]; do
  xwininfo -root -tree 2>/dev/null |
    grep -Ei "$VERDI_WINDOW_REGEX" |
    LC_ALL=C sort >"$CURRENT_WINDOWS" || true
  LC_ALL=C comm -13 "$BASELINE_WINDOWS" "$CURRENT_WINDOWS" >"$NEW_WINDOWS"
  VERDI_WINDOWS="$(sed -n '1,$p' "$NEW_WINDOWS")"
  if [ -n "$VERDI_WINDOWS" ] && grep -Eq "$VERDI_READY_REGEX" "$NEW_WINDOWS"; then
    VERDI_READY=1
    VERDI_TITLE_CONFIRMED=1
    break
  fi
  if [ -n "$VERDI_WINDOWS" ] && [ "$elapsed" -ge "$VERDI_GENERIC_READY_DELAY" ] &&
     verdi_launch_uses_elab_db; then
    VERDI_READY=1
    break
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done

pgrep -f '[v]erdi|[N]ovas|[d]ebussy' |
  LC_ALL=C sort >"$CURRENT_PIDS" || true
LC_ALL=C comm -13 "$BASELINE_PIDS" "$CURRENT_PIDS" >"$NEW_PIDS"
VERDI_PROCESSES="$(pgrep -af '[v]erdi|[N]ovas|[d]ebussy' || true)"

if [ "$VERDI_READY" -ne 1 ]; then
  echo "Verdi-related processes:" >&2
  printf '%s\n' "$VERDI_PROCESSES" >&2
  echo "New Verdi-related windows:" >&2
  printf '%s\n' "$VERDI_WINDOWS" >&2
  echo "Verdi log:" >&2
  sed -n '1,200p' "$VERDI_LOG" >&2
  fail "no new Verdi X11 window appeared within ${GUI_START_TIMEOUT}s"
fi

if [ "$VERDI_TITLE_CONFIRMED" -eq 1 ]; then
  echo "Verdi GUI loaded elaborated top 'top' after ${elapsed}s:"
else
  echo "Verdi GUI window detected after ${elapsed}s; title format differs from the tested Verdi release:"
fi
printf '%s\n' "$VERDI_WINDOWS"

cd "$PROJECT_ROOT"
POS_INVENTORY="$TEST_ROOT/positive_inventory.json"
POS_REPORT="$TEST_ROOT/positive_report.json"
POS_CSV="$TEST_ROOT/positive_report.csv"

# The checker receives only the elaborated KDB as its design input. RTL and
# filelist arguments are intentionally not accepted by this command.
"$PYTHON_BIN" -m rscheck check \
  --excel "$PROJECT_ROOT/examples/specs.csv" \
  --config "$PROJECT_ROOT/config/rscheck.example.json" \
  --sheet 1 \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --npi-timeout "$NPI_TIMEOUT" \
  --keep-inventory "$POS_INVENTORY" \
  --json-report "$POS_REPORT" \
  --csv-report "$POS_CSV"

[ -s "$POS_INVENTORY" ] || fail "positive inventory was not written"
[ -s "$POS_REPORT" ] || fail "positive JSON report was not written"
[ -s "$POS_CSV" ] || fail "positive CSV report was not written"

"$PYTHON_BIN" - "$POS_REPORT" "$POS_INVENTORY" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    report = json.load(stream)
with open(sys.argv[2], "r", encoding="utf-8") as stream:
    inventory = json.load(stream)

if report.get("schema_version") != 2:
    raise SystemExit("unexpected report schema: {!r}".format(report.get("schema_version")))
if inventory.get("schema_version") != 2:
    raise SystemExit(
        "unexpected inventory schema: {!r}".format(inventory.get("schema_version"))
    )

summary = report["summary"]

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

instances = {
    instance["name"]: instance
    for instance in inventory["positions"]["top.u_tile"]["instances"]
}
for name in ("AAAA_BBB_C0", "AAAA_BBB_C1", "CTRL_RS_D0"):
    parameters = instances[name].get("parameters")
    if not isinstance(parameters, dict) or parameters.get("RS_CFG_EN") != "0":
        raise SystemExit(
            "instance {} does not expose final RS_CFG_EN=0: {!r}".format(
                name, parameters
            )
        )
    if parameters.get("WIDTH") != "1":
        raise SystemExit(
            "instance {} did not expose unrelated WIDTH=1: {!r}".format(
                name, parameters
            )
        )
for name in ("u_crg", "u_aux_crg"):
    if instances[name].get("parameters") != {}:
        raise SystemExit(
            "parameterless instance {} did not produce an empty map: {!r}".format(
                name, instances[name].get("parameters")
            )
        )

for row in report["rows"]:
    if row["spec"].get("RS_CFG_EN") != "假门控":
        raise SystemExit("report lost RS_CFG_EN Excel evidence: {!r}".format(row["spec"]))
    for instance in row["matched_instances"]:
        if instance.get("parameters", {}).get("RS_CFG_EN") != "0":
            raise SystemExit(
                "report lost RS_CFG_EN parameter evidence: {!r}".format(instance)
            )
print("positive summary OK:", summary)
print("RS_CFG_EN inventory/report evidence OK")
PY

trap - ERR
echo "PASS: Verdi GUI launch and online NPI check completed."
echo "ELAB_DB=$ELAB_DB"
echo "REPORT=$POS_REPORT"
echo "VERDI_LOG=$VERDI_LOG"
case "$DISPLAY" in
  localhost:*|127.0.0.1:*)
    if [ "$KEEP_VERDI_GUI" = 1 ]; then
      echo "Verdi uses SSH X11 forwarding on DISPLAY=$DISPLAY; keep this SSH connection open."
    else
      echo "Verdi used SSH X11 forwarding on DISPLAY=$DISPLAY and will now be closed."
    fi
    ;;
  *)
    if [ "$KEEP_VERDI_GUI" = 1 ]; then
      echo "Verdi remains open on DISPLAY=$DISPLAY for manual hierarchy inspection."
    else
      echo "Verdi was visible on DISPLAY=$DISPLAY and will now be closed."
    fi
    ;;
esac
