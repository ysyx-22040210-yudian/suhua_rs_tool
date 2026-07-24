#!/usr/bin/env bash

# End-to-end test for the sample RTL: Python tests, NPI build, fresh KDB,
# Verdi/tool GUI checks, and offline GUI stability/load coverage.
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
GUI_ONLINE_ITERATIONS="${GUI_ONLINE_ITERATIONS:-3}"
GUI_STRESS_ITERATIONS="${GUI_STRESS_ITERATIONS:-100}"
GUI_LOAD_ROWS="${GUI_LOAD_ROWS:-10000}"
GUI_VISIBLE_SECONDS="${GUI_VISIBLE_SECONDS:-2}"
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
ELAB_DB=""

pid_uses_elab_db() {
  local pid=$1
  local argument
  local previous=""
  [ -n "$ELAB_DB" ] || return 1
  [ -r "/proc/$pid/cmdline" ] || return 1
  while IFS= read -r -d '' argument; do
    if [ "$previous" = "-elab" ] && [ "$argument" = "$ELAB_DB" ]; then
      return 0
    fi
    previous="$argument"
  done <"/proc/$pid/cmdline"
  return 1
}

verdi_pids_for_elab_db() {
  local cmdline
  local pid
  [ -n "$ELAB_DB" ] || return 0
  for cmdline in /proc/[0-9]*/cmdline; do
    pid="${cmdline#/proc/}"
    pid="${pid%/cmdline}"
    if pid_uses_elab_db "$pid"; then
      printf '%s\n' "$pid"
    fi
  done
}

stop_owned_verdi() {
  local -a pids=()
  local pid
  local alive
  mapfile -t pids < <(verdi_pids_for_elab_db)
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for _ in {1..30}; do
    alive=0
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null && pid_uses_elab_db "$pid"; then
        alive=1
      fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.1
  done
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null && pid_uses_elab_db "$pid"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  if [ -n "$VERDI_LAUNCH_PID" ]; then
    wait "$VERDI_LAUNCH_PID" 2>/dev/null || true
  fi
}

cleanup() {
  local rc=$?
  trap - EXIT
  if [ "$KEEP_VERDI_GUI" = 0 ]; then
    stop_owned_verdi
  fi
  exit "$rc"
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

collector_error_pattern='error\[(ELAB_DB|NPI_INIT|NPI_LOAD|NPI_END|OUTPUT|INTERNAL)\]|NPI collector (timed out|exited with code|succeeded but did not create|failed to start)|npi_load_design failed'

assert_no_collector_errors() {
  local log_path=$1
  if grep -Eiq "$collector_error_pattern" "$log_path"; then
    echo "Collector/NPI failure marker found in $log_path:" >&2
    grep -Ein "$collector_error_pattern" "$log_path" >&2 || true
    fail "collector/NPI log check failed"
  fi
}

assert_no_unexpected_collector_logs() {
  local log_path
  local failed=0
  while IFS= read -r -d '' log_path; do
    if grep -Eiq "$collector_error_pattern|fatal" "$log_path"; then
      echo "Unexpected collector/NPI error log: $log_path" >&2
      grep -Ein "$collector_error_pattern|fatal" "$log_path" >&2 || true
      failed=1
    fi
  done < <(
    find "$TEST_ROOT" -type f \
      \( -iname '*collector*.log' -o -iname '*npi*.log' \) -print0
  )
  [ "$failed" -eq 0 ] || fail "unexpected collector/NPI fatal or error log found"
}

assert_verdi_still_ready() {
  local -a pids=()
  mapfile -t pids < <(verdi_pids_for_elab_db)
  [ "${#pids[@]}" -gt 0 ] || fail "Verdi process for the fresh elaborated KDB exited during the test"

  xwininfo -root -tree 2>/dev/null |
    grep -Ei "$VERDI_WINDOW_REGEX" |
    LC_ALL=C sort >"$CURRENT_WINDOWS" || true
  LC_ALL=C comm -13 "$BASELINE_WINDOWS" "$CURRENT_WINDOWS" >"$NEW_WINDOWS"
  if [ "$VERDI_TITLE_CONFIRMED" -eq 1 ]; then
    grep -Eq "$VERDI_READY_REGEX" "$NEW_WINDOWS" ||
      fail "the Verdi top window disappeared during the test"
  else
    [ -s "$NEW_WINDOWS" ] || fail "the Verdi GUI window disappeared during the test"
  fi

  if grep -Eiq \
    'segmentation fault|core dumped|fatal([ :]|$)|license (checkout )?failed|cannot (checkout|obtain).*license' \
    "$VERDI_LOG"; then
    echo "Verdi failure marker found in $VERDI_LOG:" >&2
    grep -Ein \
      'segmentation fault|core dumped|fatal([ :]|$)|license (checkout )?failed|cannot (checkout|obtain).*license' \
      "$VERDI_LOG" >&2 || true
    fail "Verdi log check failed"
  fi
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

for numeric_setting in \
  "$GUI_START_TIMEOUT" \
  "$VERDI_GENERIC_READY_DELAY" \
  "$NPI_TIMEOUT" \
  "$GUI_ONLINE_ITERATIONS" \
  "$GUI_STRESS_ITERATIONS" \
  "$GUI_LOAD_ROWS" \
  "$GUI_VISIBLE_SECONDS"; do
  case "$numeric_setting" in
    ''|*[!0-9]*) fail "GUI/NPI timeout, iteration, row, and visibility settings must be non-negative integers" ;;
  esac
done
[ "$GUI_START_TIMEOUT" -gt 0 ] || fail "GUI_START_TIMEOUT must be greater than zero"
[ "$NPI_TIMEOUT" -gt 0 ] || fail "NPI_TIMEOUT must be greater than zero"
[ "$GUI_ONLINE_ITERATIONS" -gt 0 ] || fail "GUI_ONLINE_ITERATIONS must be greater than zero"
[ "$GUI_STRESS_ITERATIONS" -gt 0 ] || fail "GUI_STRESS_ITERATIONS must be greater than zero"
[ "$GUI_LOAD_ROWS" -gt 0 ] || fail "GUI_LOAD_ROWS must be greater than zero"

if ! gui_session_resolve; then
  exit 1
fi

echo "GUI access OK: source=$GUI_SESSION_SOURCE user=$GUI_SESSION_SELECTED_USER DISPLAY=$DISPLAY"
if [ "$GUI_PROBE_ONLY" -eq 1 ]; then
  echo "GUI probe PASS"
  exit 0
fi

for command_name in xwininfo make ldd mktemp nohup sort comm tee grep find pgrep sed; do
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

if [ -n "${LM_LICENSE_FILE:-}" ] && [ -z "${SNPSLMD_LICENSE_FILE:-}" ]; then
  export SNPSLMD_LICENSE_FILE="$LM_LICENSE_FILE"
elif [ -n "${SNPSLMD_LICENSE_FILE:-}" ] && [ -z "${LM_LICENSE_FILE:-}" ]; then
  export LM_LICENSE_FILE="$SNPSLMD_LICENSE_FILE"
fi
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
  pid_uses_elab_db "$VERDI_LAUNCH_PID"
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
POS_LOG="$TEST_ROOT/positive_console.log"

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
  --csv-report "$POS_CSV" \
  2>&1 | tee "$POS_LOG"

[ -s "$POS_INVENTORY" ] || fail "positive inventory was not written"
[ -s "$POS_REPORT" ] || fail "positive JSON report was not written"
[ -s "$POS_CSV" ] || fail "positive CSV report was not written"
grep -Fq 'RESULT: PASS | rows=2 errors=0 warnings=0' "$POS_LOG"
assert_no_collector_errors "$POS_LOG"

"$PYTHON_BIN" - "$POS_REPORT" "$POS_INVENTORY" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    report = json.load(stream)
with open(sys.argv[2], "r", encoding="utf-8") as stream:
    inventory = json.load(stream)

if report.get("schema_version") != 3:
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
expected_group_names = ["AAAA_BBB_C{}".format(index) for index in range(6)]
actual_group_names = sorted(
    name for name in instances if name.startswith("AAAA_BBB_C")
)
if actual_group_names != expected_group_names:
    raise SystemExit(
        "AAAA_BBB physical group mismatch: expected {!r}, got {!r}".format(
            expected_group_names, actual_group_names
        )
    )

expected_rs_modes = {
    name: ("0" if name == "AAAA_BBB_C2" else "1")
    for name in expected_group_names
}
expected_rs_modes["CTRL_RS_D0"] = "1"
for name, expected_rs_mode in expected_rs_modes.items():
    parameters = instances[name].get("parameters")
    if not isinstance(parameters, dict) or parameters.get("RS_CFG_EN") != "0":
        raise SystemExit(
            "instance {} does not expose final RS_CFG_EN=0: {!r}".format(
                name, parameters
            )
        )
    if parameters.get("rs_mode") != expected_rs_mode:
        raise SystemExit(
            "instance {} has unexpected rs_mode; expected {}, got {!r}".format(
                name, expected_rs_mode, parameters.get("rs_mode")
            )
        )
    if parameters.get("WIDTH") != "1":
        raise SystemExit(
            "instance {} did not expose unrelated WIDTH=1: {!r}".format(
                name, parameters
            )
        )
rs_mode_values = [
    instances[name]["parameters"].get("rs_mode") for name in expected_group_names
]
if rs_mode_values.count("1") != 5 or rs_mode_values.count("0") != 1:
    raise SystemExit("AAAA_BBB rs_mode distribution is not five enabled, one disabled")

for name in ("u_crg", "u_aux_crg"):
    if instances[name].get("parameters") != {}:
        raise SystemExit(
            "parameterless instance {} did not produce an empty map: {!r}".format(
                name, instances[name].get("parameters")
            )
        )

rows_by_group = {row["spec"]["RS_inst"]: row for row in report["rows"]}
if set(rows_by_group) != {"AAAA_BBB", "CTRL_RS"}:
    raise SystemExit("unexpected report groups: {!r}".format(sorted(rows_by_group)))

for row in report["rows"]:
    if row["spec"].get("RS_CFG_EN") != "假门控":
        raise SystemExit("report lost RS_CFG_EN Excel evidence: {!r}".format(row["spec"]))
    expected_rule = {
        "name": "rs_pipe",
        "has_rs_cfg_en": True,
        "step_parameters": ["rs_mode"],
    }
    if row.get("module_rule") != expected_rule:
        raise SystemExit("unexpected module rule evidence: {!r}".format(row.get("module_rule")))
    for instance in row["matched_instances"]:
        if instance.get("parameters", {}).get("RS_CFG_EN") != "0":
            raise SystemExit(
                "report lost RS_CFG_EN parameter evidence: {!r}".format(instance)
            )

group_row = rows_by_group["AAAA_BBB"]
step_check = group_row.get("step_check")
if not isinstance(step_check, dict):
    raise SystemExit("AAAA_BBB row has no step_check evidence")
if step_check.get("expected") != 5:
    raise SystemExit("AAAA_BBB expected step mismatch: {!r}".format(step_check))
if step_check.get("physical_instances") != 6:
    raise SystemExit("AAAA_BBB physical instance count is not 6: {!r}".format(step_check))
if step_check.get("effective_step") != 5:
    raise SystemExit("AAAA_BBB effective step is not 5: {!r}".format(step_check))

contributions = step_check.get("contributions")
if not isinstance(contributions, list):
    raise SystemExit("AAAA_BBB contributions are not an array: {!r}".format(step_check))
contribution_values = [item.get("contribution") for item in contributions]
if contribution_values != [1, 1, 0, 1, 1, 1]:
    raise SystemExit("unexpected AAAA_BBB contributions: {!r}".format(contribution_values))
if [item.get("instance", "").rsplit(".", 1)[-1] for item in contributions] != expected_group_names:
    raise SystemExit("AAAA_BBB contribution order/instances mismatch: {!r}".format(contributions))
for item, name in zip(contributions, expected_group_names):
    parameter_evidence = item.get("parameters", {}).get("rs_mode")
    expected_value = expected_rs_modes[name]
    expected_state = "zero" if expected_value == "0" else "nonzero"
    if parameter_evidence != {
        "present": True,
        "raw_value": expected_value,
        "state": expected_state,
    }:
        raise SystemExit(
            "unexpected rs_mode contribution evidence for {}: {!r}".format(
                name, parameter_evidence
            )
        )

matched_names = [item["name"] for item in group_row["matched_instances"]]
if matched_names != expected_group_names:
    raise SystemExit("AAAA_BBB matched instances mismatch: {!r}".format(matched_names))
for instance, contribution in zip(group_row["matched_instances"], contributions):
    if instance.get("step_evaluation") != contribution:
        raise SystemExit(
            "matched instance lost step evaluation evidence: {!r}".format(instance)
        )

control_step = rows_by_group["CTRL_RS"].get("step_check")
if not isinstance(control_step, dict) or (
    control_step.get("expected"),
    control_step.get("physical_instances"),
    control_step.get("effective_step"),
) != (1, 1, 1):
    raise SystemExit("unexpected CTRL_RS step evidence: {!r}".format(control_step))
print("positive summary OK:", summary)
print("dynamic step inventory/report evidence OK")
PY

ONLINE_GUI_LOG="$TEST_ROOT/online_gui_positive.log"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/test_rscheck_gui_smoke.py" \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout "$NPI_TIMEOUT" \
  --iterations "$GUI_ONLINE_ITERATIONS" \
  --visible-tab results \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$ONLINE_GUI_LOG"
grep -Fq \
  "state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=online case=positive iterations=$GUI_ONLINE_ITERATIONS" \
  "$ONLINE_GUI_LOG"
grep -Fq 'contract=elab-only schemas=report-v3/inventory-v2' "$ONLINE_GUI_LOG"
assert_no_collector_errors "$ONLINE_GUI_LOG"

ONLINE_NEGATIVE_LOG="$TEST_ROOT/online_gui_negative.log"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/test_rscheck_gui_smoke.py" \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout "$NPI_TIMEOUT" \
  --negative \
  --iterations 1 \
  --visible-tab results \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$ONLINE_NEGATIVE_LOG"
grep -Eq \
  'state=FAIL rows=行数 1 errors=错误 [1-9][0-9]* warnings=警告 0 mode=online case=negative iterations=1' \
  "$ONLINE_NEGATIVE_LOG"
grep -Fq 'contract=elab-only schemas=report-v3/inventory-v2' "$ONLINE_NEGATIVE_LOG"
assert_no_collector_errors "$ONLINE_NEGATIVE_LOG"

DEFAULT_RULE_LOG="$TEST_ROOT/offline_gui_default_rule.log"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/test_rscheck_gui_smoke.py" \
  --project-root "$PROJECT_ROOT" \
  --default-rule \
  --iterations 1 \
  --visible-tab results \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$DEFAULT_RULE_LOG"
grep -Fq \
  'state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=default-rule iterations=1' \
  "$DEFAULT_RULE_LOG"
grep -Fq \
  'rule=unregistered-default has-rs-cfg-en=true step-parameters=[] physical=2 effective=2 contributions=1,1' \
  "$DEFAULT_RULE_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$DEFAULT_RULE_LOG"

OFFLINE_STRESS_LOG="$TEST_ROOT/offline_gui_100_rounds.log"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/test_rscheck_gui_smoke.py" \
  --project-root "$PROJECT_ROOT" \
  --iterations "$GUI_STRESS_ITERATIONS" \
  --visible-tab rules \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$OFFLINE_STRESS_LOG"
grep -Fq \
  "state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=$GUI_STRESS_ITERATIONS" \
  "$OFFLINE_STRESS_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$OFFLINE_STRESS_LOG"

OFFLINE_LOAD_LOG="$TEST_ROOT/offline_gui_10000_rows.log"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/test_rscheck_gui_smoke.py" \
  --project-root "$PROJECT_ROOT" \
  --generated-rows "$GUI_LOAD_ROWS" \
  --iterations 1 \
  --timeout "$NPI_TIMEOUT" \
  --visible-tab results \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$OFFLINE_LOAD_LOG"
grep -Fq \
  "state=PASS rows=行数 $GUI_LOAD_ROWS errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1" \
  "$OFFLINE_LOAD_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$OFFLINE_LOAD_LOG"

assert_no_unexpected_collector_logs
assert_verdi_still_ready

trap - ERR
echo "PASS: fresh KDB online positive/negative GUI checks and offline GUI stress suite completed."
echo "ELAB_DB=$ELAB_DB"
echo "REPORT=$POS_REPORT"
echo "VERDI_LOG=$VERDI_LOG"
echo "GUI_LOGS=$ONLINE_GUI_LOG,$ONLINE_NEGATIVE_LOG,$DEFAULT_RULE_LOG,$OFFLINE_STRESS_LOG,$OFFLINE_LOAD_LOG"
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
