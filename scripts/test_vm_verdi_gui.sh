#!/usr/bin/env bash

# End-to-end test for the sample RTL: Python tests, NPI build, fresh KDB,
# Verdi/tool GUI checks, and offline GUI stability/load coverage.
{ set +x; } 2>/dev/null
set -Ee -o pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
VERDI_ENV_FILE="${VERDI_ENV_FILE-}"
RSCHECK_VERDI_ENV_APPLIED="${RSCHECK_VERDI_ENV_APPLIED:-0}"
RSCHECK_FIXED_PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
RSCHECK_FIXED_OUTPUT_BASE="${OUTPUT_BASE:-$RSCHECK_FIXED_PROJECT_ROOT/output}"
RSCHECK_LOAD_SITE_ENV=1
if [ "$#" -eq 1 ] && [ "${1-}" = "--gui-probe-only" ]; then
  RSCHECK_LOAD_SITE_ENV=0
fi

case "$RSCHECK_VERDI_ENV_APPLIED" in
  0|1) ;;
  *)
    echo "ERROR: RSCHECK_VERDI_ENV_APPLIED must be 0 or 1" >&2
    exit 1
    ;;
esac

if [ -n "$VERDI_ENV_FILE" ] &&
   [ "$RSCHECK_VERDI_ENV_APPLIED" -eq 0 ] &&
   [ "$RSCHECK_LOAD_SITE_ENV" -eq 1 ]; then
  case "$VERDI_ENV_FILE" in
    /*) ;;
    *)
      echo "ERROR: VERDI_ENV_FILE must be an absolute path" >&2
      exit 1
      ;;
  esac
  [ -f "$VERDI_ENV_FILE" ] && [ -r "$VERDI_ENV_FILE" ] || {
    echo "ERROR: VERDI_ENV_FILE is not a readable regular file" >&2
    exit 1
  }

  BASH_BIN="$(command -v bash 2>/dev/null || true)"
  [ -n "$BASH_BIN" ] || {
    echo "ERROR: bash is required to load VERDI_ENV_FILE" >&2
    exit 1
  }
  SITE_ENV_RUNNER="$SCRIPT_DIR/lib/run_with_env_file.sh"
  [ -f "$SITE_ENV_RUNNER" ] || {
    echo "ERROR: site environment runner not found: $SITE_ENV_RUNNER" >&2
    exit 1
  }

  site_env_command=(
    /usr/bin/env
    RSCHECK_VERDI_ENV_APPLIED=1
    VERDI_ENV_FILE=
    "PROJECT_ROOT=$RSCHECK_FIXED_PROJECT_ROOT"
    "OUTPUT_BASE=$RSCHECK_FIXED_OUTPUT_BASE"
  )
  site_env_command+=("$BASH_BIN" "$SCRIPT_DIR/test_vm_verdi_gui.sh" "$@")

  exec "$BASH_BIN" "$SITE_ENV_RUNNER" \
    "$VERDI_ENV_FILE" "${site_env_command[@]}"
fi

PROJECT_ROOT="$RSCHECK_FIXED_PROJECT_ROOT"
VERDI_HOME="${VERDI_HOME:-${NOVAS_INST_DIR:-}}"
VERDI_BIN="${VERDI_BIN-}"
VERICOM_BIN="${VERICOM_BIN-}"
ELABCOM_BIN="${ELABCOM_BIN-}"
NPI_PLATFORM="${NPI_PLATFORM-}"
NPI_INC_DIR="${NPI_INC_DIR-}"
NPI_LIB_DIR="${NPI_LIB_DIR-}"
NPI_L1_INC_DIR="${NPI_L1_INC_DIR-}"
NPI_L1_LIB_DIR="${NPI_L1_LIB_DIR-}"
GUI_START_TIMEOUT="${GUI_START_TIMEOUT:-180}"
VERDI_WINDOW_REGEX="${VERDI_WINDOW_REGEX:-verdi|novas|debussy}"
VERDI_READY_REGEX="${VERDI_READY_REGEX:-<Verdi:nTraceMain[^>]*>[[:space:]]+top([[:space:]]|$)}"
NPI_TIMEOUT="${NPI_TIMEOUT:-180}"
OUTPUT_BASE="$RSCHECK_FIXED_OUTPUT_BASE"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CXX="${CXX:-g++}"
GUI_ONLINE_ITERATIONS="${GUI_ONLINE_ITERATIONS:-3}"
GUI_CLK_WITHOUT_RST_ITERATIONS="${GUI_CLK_WITHOUT_RST_ITERATIONS:-20}"
GUI_STRESS_ITERATIONS="${GUI_STRESS_ITERATIONS:-100}"
GUI_LOAD_ROWS="${GUI_LOAD_ROWS:-10000}"
GUI_VISIBLE_SECONDS="${GUI_VISIBLE_SECONDS:-2}"
PYTHON_ENABLE_IS_SET="${PYTHON_ENABLE+x}"
GCC_ENABLE_IS_SET="${GCC_ENABLE+x}"
PYTHON_ENABLE="${PYTHON_ENABLE-}"
GCC_ENABLE="${GCC_ENABLE-}"
KEEP_VERDI_GUI="${KEEP_VERDI_GUI:-0}"
VERDI_AUTO_LICENSE_IMPORT="${VERDI_AUTO_LICENSE_IMPORT:-1}"

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

# BEGIN VERDI LICENSE ENVIRONMENT HELPERS
resolve_verdi_license_environment() {
  local current_user=""
  local imported_lm=""
  local imported_snps=""
  local license_output=""
  local line=""

  if [ -z "${LM_LICENSE_FILE:-}" ] && [ -z "${SNPSLMD_LICENSE_FILE:-}" ] &&
     [ "${VERDI_AUTO_LICENSE_IMPORT:-1}" = 1 ]; then
    current_user="$(id -un 2>/dev/null || true)"
    if [ "$(id -u 2>/dev/null || true)" = 0 ] &&
       [ -n "${GUI_SESSION_SELECTED_USER-}" ] &&
       [ "$GUI_SESSION_SELECTED_USER" != "$current_user" ]; then
      if ! command -v runuser >/dev/null 2>&1; then
        echo "WARNING: runuser is unavailable; continuing with Verdi native license diagnostics" >&2
      elif license_output="$(
        runuser -u "$GUI_SESSION_SELECTED_USER" -- bash -lc '
          printf "__RSCHECK_LM_LICENSE_FILE__%s\n" "${LM_LICENSE_FILE-}"
          printf "__RSCHECK_SNPSLMD_LICENSE_FILE__%s\n" "${SNPSLMD_LICENSE_FILE-}"
        ' 2>/dev/null
      )"; then
        while IFS= read -r line; do
          case "$line" in
            __RSCHECK_LM_LICENSE_FILE__*)
              imported_lm="${line#__RSCHECK_LM_LICENSE_FILE__}"
              ;;
            __RSCHECK_SNPSLMD_LICENSE_FILE__*)
              imported_snps="${line#__RSCHECK_SNPSLMD_LICENSE_FILE__}"
              ;;
          esac
        done <<<"$license_output"
        if [ -n "$imported_lm" ]; then
          export LM_LICENSE_FILE="$imported_lm"
        fi
        if [ -n "$imported_snps" ]; then
          export SNPSLMD_LICENSE_FILE="$imported_snps"
        fi
        if [ -n "$imported_lm" ] || [ -n "$imported_snps" ]; then
          echo "License environment: imported from selected GUI user's bash initialization"
        else
          echo "WARNING: selected GUI user's bash initialization provided no license variables; continuing with Verdi native diagnostics" >&2
        fi
      else
        echo "WARNING: selected GUI user's bash initialization failed; continuing with Verdi native diagnostics" >&2
      fi
    fi
  fi

  if [ -n "${LM_LICENSE_FILE:-}" ] && [ -z "${SNPSLMD_LICENSE_FILE:-}" ]; then
    export SNPSLMD_LICENSE_FILE="$LM_LICENSE_FILE"
  elif [ -n "${SNPSLMD_LICENSE_FILE:-}" ] && [ -z "${LM_LICENSE_FILE:-}" ]; then
    export LM_LICENSE_FILE="$SNPSLMD_LICENSE_FILE"
  fi
}
# END VERDI LICENSE ENVIRONMENT HELPERS

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
  grep -Eq "$VERDI_READY_REGEX" "$NEW_WINDOWS" ||
    fail "the Verdi window for elaborated top 'top' disappeared during the test"

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
  "$NPI_TIMEOUT" \
  "$GUI_ONLINE_ITERATIONS" \
  "$GUI_CLK_WITHOUT_RST_ITERATIONS" \
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
[ "$GUI_CLK_WITHOUT_RST_ITERATIONS" -gt 0 ] ||
  fail "GUI_CLK_WITHOUT_RST_ITERATIONS must be greater than zero"
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

case "$VERDI_AUTO_LICENSE_IMPORT" in
  0|1) ;;
  *) fail "VERDI_AUTO_LICENSE_IMPORT must be 0 or 1" ;;
esac
resolve_verdi_license_environment

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
NPI_L1_INC_DIR="${NPI_L1_INC_DIR:-$VERDI_HOME/share/NPI/L1/C/inc}"
if [ -z "$NPI_L1_LIB_DIR" ]; then
  if [ -f "$NPI_LIB_DIR/libnpiL1.so" ]; then
    NPI_L1_LIB_DIR="$NPI_LIB_DIR"
  else
    NPI_PLATFORM_LOWER="$(printf '%s' "$NPI_PLATFORM" | tr '[:upper:]' '[:lower:]')"
    if [ -f "$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM_LOWER/libnpiL1.so" ]; then
      NPI_L1_LIB_DIR="$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM_LOWER"
    else
      for candidate_library in "$VERDI_HOME"/share/NPI/lib/*/libnpiL1.so; do
        [ -f "$candidate_library" ] || continue
        NPI_L1_LIB_DIR="${candidate_library%/libnpiL1.so}"
        break
      done
    fi
  fi
fi
[ -f "$NPI_INC_DIR/npi.h" ] || fail "NPI header not found: $NPI_INC_DIR/npi.h"
[ -f "$NPI_LIB_DIR/libNPI.so" ] || fail "libNPI.so not found; set NPI_LIB_DIR"
[ -f "$NPI_L1_INC_DIR/npi_L1.h" ] ||
  fail "NPI L1 header not found; set NPI_L1_INC_DIR"
[ -f "$NPI_L1_LIB_DIR/libnpiL1.so" ] ||
  fail "libnpiL1.so not found; set NPI_L1_LIB_DIR"

NPI_RUNTIME_LIB_DIRS="$NPI_LIB_DIR"
if [ "$NPI_L1_LIB_DIR" != "$NPI_LIB_DIR" ]; then
  NPI_RUNTIME_LIB_DIRS="$NPI_L1_LIB_DIR:$NPI_RUNTIME_LIB_DIRS"
fi
export LD_LIBRARY_PATH="$NPI_RUNTIME_LIB_DIRS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

[ -d "$PROJECT_ROOT" ] || fail "PROJECT_ROOT is not a directory: $PROJECT_ROOT"
[ -f "$PROJECT_ROOT/pyproject.toml" ] || fail "not an rtl-rs-check repository: $PROJECT_ROOT"

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
  NPI_L1_INC="$NPI_L1_INC_DIR" \
  NPI_L1_LIB="$NPI_L1_LIB_DIR" \
  CXX="$CXX"
COLLECTOR="$NPI_BUILD_DIR/rs_npi_collector"
[ -x "$COLLECTOR" ] || fail "collector was not built: $COLLECTOR"
ldd "$COLLECTOR" | tee "$TEST_ROOT/collector_ldd.txt"
grep -q 'libNPI.so' "$TEST_ROOT/collector_ldd.txt"
grep -q 'libnpiL1.so' "$TEST_ROOT/collector_ldd.txt"
if grep -q 'not found' "$TEST_ROOT/collector_ldd.txt"; then
  fail "collector has unresolved shared libraries"
fi

PARTIAL_ELAB_ROOT="$TEST_ROOT/partial_load_elab"
PARTIAL_ELAB_DB="$PARTIAL_ELAB_ROOT/partial.elab++"
PARTIAL_POSITIONS="$TEST_ROOT/partial_load_positions.txt"
PARTIAL_INVENTORY="$TEST_ROOT/partial_load_inventory.json"
PARTIAL_REPORT="$TEST_ROOT/partial_load_report.json"
PARTIAL_STDOUT="$TEST_ROOT/partial_load.stdout"
PARTIAL_STDERR="$TEST_ROOT/partial_load.stderr"
PARTIAL_CHECK_LOG="$TEST_ROOT/partial_load_check.log"
PARTIAL_GUI_LOG="$TEST_ROOT/partial_load_gui.log"
mkdir -p "$PARTIAL_ELAB_ROOT"

cd "$PARTIAL_ELAB_ROOT"
"$VERICOM_BIN" +define+RSCHECK_PARTIAL_LOAD_FIXTURE \
  -sv "$PROJECT_ROOT/examples/rtl/rs_example.sv"
"$ELABCOM_BIN" -top top -elab "$PARTIAL_ELAB_DB"
[ -d "$PARTIAL_ELAB_DB" ] || fail "partial-load elabcom did not create a KDB"
printf 'top.u_tile\n' >"$PARTIAL_POSITIONS"

set +e
LD_LIBRARY_PATH="$NPI_RUNTIME_LIB_DIRS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  "$COLLECTOR" \
    --positions "$PARTIAL_POSITIONS" \
    --output "$PARTIAL_INVENTORY" \
    --clk-port clk \
    --rst-port rst \
    --elab-db "$PARTIAL_ELAB_DB" \
    >"$PARTIAL_STDOUT" 2>"$PARTIAL_STDERR"
PARTIAL_COLLECTOR_RC=$?
set -e
cat "$PARTIAL_STDOUT"
cat "$PARTIAL_STDERR"
[ "$PARTIAL_COLLECTOR_RC" -eq 0 ] ||
  fail "collector rejected a partial KDB whose top remains queryable"
grep -Fq 'warning[NPI_LOAD_PARTIAL]' "$PARTIAL_STDERR"

"$PYTHON_BIN" - "$PARTIAL_INVENTORY" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    inventory = json.load(stream)
notices = inventory.get("notices")
if not isinstance(notices, list) or len(notices) != 1:
    raise SystemExit("partial-load inventory must contain one notice: {!r}".format(notices))
if "top instance(s) remain queryable" not in notices[0]:
    raise SystemExit("unexpected partial-load notice: {!r}".format(notices[0]))
if inventory.get("warnings") != []:
    raise SystemExit("partial-load fixture has unresolved traversal warnings")
position = inventory.get("positions", {}).get("top.u_tile", {})
if position.get("found") is not True:
    raise SystemExit("top.u_tile was not queryable after partial load")
instances = position.get("instances", [])
names = {item.get("name") for item in instances}
if not {"CTRL_RS_D0", "AAAA_BBB_C0", "CLK_ONLY_RS"}.issubset(names):
    raise SystemExit("partial-load inventory is missing expected RS instances: {!r}".format(names))
clk_only = next(item for item in instances if item.get("name") == "CLK_ONLY_RS")
clk_only_ports = clk_only.get("ports")
if clk_only.get("module") != "rs_clk_only":
    raise SystemExit("partial clk-only module mismatch: {!r}".format(clk_only))
if not isinstance(clk_only_ports, dict) or set(clk_only_ports) != {"clk", "d", "q"}:
    raise SystemExit("partial clk-only formal ports mismatch: {!r}".format(clk_only_ports))
if clk_only_ports.get("clk", {}).get("connection") != "top.u_tile.clk_rs":
    raise SystemExit("partial clk-only high connection mismatch: {!r}".format(clk_only_ports))
if "rst" in clk_only_ports or "rst_n" in clk_only_ports:
    raise SystemExit("partial clk-only unexpectedly has an rst formal port: {!r}".format(clk_only_ports))
expected_ports_by_module = {
    "rs_pipe": {"clk", "rst", "d", "q"},
    "rs_custom": {"clock_i", "reset_ni", "d", "q"},
    "rs_clk_only": {"clk", "d", "q"},
    "crg_core": {"ref_clk", "clk_out"},
    "crg_aux": {"ref_clk", "clk_out"},
}
for instance in position.get("instances", []):
    module = instance.get("module")
    if module not in expected_ports_by_module:
        continue
    ports = instance.get("ports")
    if not isinstance(ports, dict) or set(ports) != expected_ports_by_module[module]:
        raise SystemExit(
            "partial-load formal-port inventory mismatch for {}: {!r}".format(
                instance.get("full_name"), ports
            )
        )
    if instance.get("clk_sources") != []:
        raise SystemExit(
            "clock-source tracing must be disabled for {}: {!r}".format(
                instance.get("full_name"), instance.get("clk_sources")
            )
        )
print("partial NPI load evidence OK: load reported errors but requested RTL remained queryable")
print("partial NPI formal-port L0/L1 inventory evidence OK; clock-source tracing disabled")
print("partial clk-present/rst-absent evidence OK: CLK_ONLY_RS ports=clk,d,q")
PY

cd "$PROJECT_ROOT"
"$PYTHON_BIN" -m rscheck check \
  --excel "$PROJECT_ROOT/examples/specs.csv" \
  --config "$PROJECT_ROOT/config/rscheck.example.json" \
  --collector "$COLLECTOR" \
  --elab-db "$PARTIAL_ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --npi-timeout "$NPI_TIMEOUT" \
  --json-report "$PARTIAL_REPORT" \
  2>&1 | tee "$PARTIAL_CHECK_LOG"
grep -Fq 'RESULT: PASS | rows=2 errors=0 warnings=1' "$PARTIAL_CHECK_LOG"
grep -Fq '[WARNING] NPI_LOAD_PARTIAL:' "$PARTIAL_CHECK_LOG"
assert_no_collector_errors "$PARTIAL_CHECK_LOG"

"$PYTHON_BIN" - "$PARTIAL_REPORT" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    report = json.load(stream)
if report.get("summary") != {
    "passed": True,
    "rows": 2,
    "passed_rows": 2,
    "failed_rows": 0,
    "errors": 0,
    "warnings": 1,
}:
    raise SystemExit("unexpected partial-load summary: {!r}".format(report.get("summary")))
findings = report.get("global_findings", [])
if len(findings) != 1 or findings[0].get("code") != "NPI_LOAD_PARTIAL":
    raise SystemExit("partial-load report warning is missing: {!r}".format(findings))
PY

"$PYTHON_BIN" "$PROJECT_ROOT/scripts/test_rscheck_gui_smoke.py" \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$PARTIAL_ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout "$NPI_TIMEOUT" \
  --iterations 1 \
  --expect-partial-load \
  --visible-tab results \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$PARTIAL_GUI_LOG"
grep -Fq \
  'state=PASS rows=行数 2 errors=错误 0 warnings=警告 1 mode=online case=partial-load iterations=1' \
  "$PARTIAL_GUI_LOG"
grep -Fq 'notice=NPI_LOAD_PARTIAL' "$PARTIAL_GUI_LOG"
grep -Fq 'contract=elab-only' "$PARTIAL_GUI_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$PARTIAL_GUI_LOG"
assert_no_collector_errors "$PARTIAL_GUI_LOG"

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

elapsed=0
VERDI_WINDOWS=""
VERDI_READY=0
while [ "$elapsed" -lt "$GUI_START_TIMEOUT" ]; do
  xwininfo -root -tree 2>/dev/null |
    grep -Ei "$VERDI_WINDOW_REGEX" |
    LC_ALL=C sort >"$CURRENT_WINDOWS" || true
  LC_ALL=C comm -13 "$BASELINE_WINDOWS" "$CURRENT_WINDOWS" >"$NEW_WINDOWS"
  VERDI_WINDOWS="$(sed -n '1,$p' "$NEW_WINDOWS")"
  if [ -n "$VERDI_WINDOWS" ] && grep -Eq "$VERDI_READY_REGEX" "$NEW_WINDOWS"; then
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
  fail "no new Verdi X11 window title matched VERDI_READY_REGEX for elaborated top 'top' within ${GUI_START_TIMEOUT}s"
fi

echo "Verdi GUI loaded elaborated top 'top' after ${elapsed}s:"
printf '%s\n' "$VERDI_WINDOWS"

cd "$PROJECT_ROOT"
POS_INVENTORY="$TEST_ROOT/positive_inventory.json"
POS_REPORT="$TEST_ROOT/positive_report.json"
POS_CSV="$TEST_ROOT/positive_report.csv"
POS_LOG="$TEST_ROOT/positive_console.log"

"$PYTHON_BIN" - \
  "$PROJECT_ROOT/examples/specs.csv" \
  "$PROJECT_ROOT/config/rscheck.example.json" <<'PY'
import csv
import json
import sys

internal_fields = [
    "Intf_type",
    "RS_module",
    "RS_inst",
    "position",
    "step",
    "clk",
    "rst",
    "CRG_source",
    "RS_CFG_EN",
]
expected_headers = [
    "接口分类",
    "模块类型",
    "实例组",
    "位置简称",
    "有效拍数",
    "时钟连接",
    "复位连接",
    "时钟源模块",
    "假门控标记",
]
with open(sys.argv[1], "r", encoding="utf-8-sig", newline="") as stream:
    actual_headers = next(csv.reader(stream))
with open(sys.argv[2], "r", encoding="utf-8") as stream:
    config = json.load(stream)

if actual_headers != expected_headers:
    raise SystemExit(
        "example must use arbitrary business headers: {!r}".format(actual_headers)
    )
if set(actual_headers) & set(internal_fields):
    raise SystemExit("example headers unexpectedly reuse internal field names")
if config.get("excel", {}).get("validate_headers") is not False:
    raise SystemExit("strict header validation must be disabled by default")
expected_columns = {
    field_name: index for index, field_name in enumerate(internal_fields, start=1)
}
if config.get("columns") != expected_columns:
    raise SystemExit("unexpected internal-field column mapping: {!r}".format(config.get("columns")))
print("column mapping evidence OK: arbitrary headers -> internal fields by 1-based index")
PY

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

"$PYTHON_BIN" - "$POS_REPORT" "$POS_INVENTORY" "$POS_CSV" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    report = json.load(stream)
with open(sys.argv[2], "r", encoding="utf-8") as stream:
    inventory = json.load(stream)
with open(sys.argv[3], "r", encoding="utf-8-sig", newline="") as stream:
    csv_rows = list(csv.DictReader(stream))

if report.get("schema_version") != 3:
    raise SystemExit("unexpected report schema: {!r}".format(report.get("schema_version")))
if inventory.get("schema_version") != 2:
    raise SystemExit(
        "unexpected inventory schema: {!r}".format(inventory.get("schema_version"))
    )

positions = inventory.get("positions")
if not isinstance(positions, dict) or set(positions) != {"top.u_tile"}:
    raise SystemExit(
        "collector must receive only resolved full paths, got {!r}".format(positions)
    )
if "tile_core" in positions:
    raise SystemExit("Excel position alias leaked into NPI inventory positions")
if len(csv_rows) != 2 or any(
    row.get("position") != "top.u_tile"
    or row.get("position_alias") != "tile_core"
    for row in csv_rows
):
    raise SystemExit("CSV report lost resolved position/alias evidence: {!r}".format(csv_rows))

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
expected_ports_by_module = {
    "rs_pipe": {"clk", "rst", "d", "q"},
    "rs_custom": {"clock_i", "reset_ni", "d", "q"},
    "rs_clk_only": {"clk", "d", "q"},
    "crg_core": {"ref_clk", "clk_out"},
    "crg_aux": {"ref_clk", "clk_out"},
}
for instance in instances.values():
    module = instance.get("module")
    if module not in expected_ports_by_module:
        continue
    ports = instance.get("ports")
    if not isinstance(ports, dict) or set(ports) != expected_ports_by_module[module]:
        raise SystemExit(
            "formal-port inventory mismatch for {}: {!r}".format(
                instance.get("full_name"), ports
            )
        )
    if instance.get("clk_sources") != []:
        raise SystemExit(
            "clock-source tracing must be disabled for {}: {!r}".format(
                instance.get("full_name"), instance.get("clk_sources")
            )
        )
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
    if not isinstance(parameters, dict) or parameters.get("RS_CRG_EN") != "0":
        raise SystemExit(
            "instance {} does not expose final RS_CRG_EN=0: {!r}".format(
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
if set(rows_by_group) != {"AAAA_BBB", "CTRL_RS_D0"}:
    raise SystemExit("unexpected report groups: {!r}".format(sorted(rows_by_group)))

for row in report["rows"]:
    if row["spec"].get("position") != "top.u_tile":
        raise SystemExit(
            "report did not use resolved full position: {!r}".format(row["spec"])
        )
    if row["spec"].get("position_alias") != "tile_core":
        raise SystemExit(
            "report lost Excel position alias: {!r}".format(row["spec"])
        )
    if row["spec"].get("RS_CFG_EN") != "假门控":
        raise SystemExit("report lost RS_CFG_EN Excel evidence: {!r}".format(row["spec"]))
    expected_rule = {
        "name": "rs_pipe",
        "has_rs_cfg_en": True,
        "step_parameters": ["rs_mode"],
        "clk_port": "clk",
        "rst_port": "rst",
    }
    if row.get("module_rule") != expected_rule:
        raise SystemExit("unexpected module rule evidence: {!r}".format(row.get("module_rule")))
    for instance in row["matched_instances"]:
        if instance.get("parameters", {}).get("RS_CRG_EN") != "0":
            raise SystemExit(
                "report lost RS_CRG_EN parameter evidence: {!r}".format(instance)
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

control_row = rows_by_group["CTRL_RS_D0"]
control_names = [item["name"] for item in control_row["matched_instances"]]
if control_names != ["CTRL_RS_D0"]:
    raise SystemExit(
        "CTRL_RS_D0 exact-name match mismatch: {!r}".format(control_names)
    )

control_step = control_row.get("step_check")
if not isinstance(control_step, dict) or (
    control_step.get("expected"),
    control_step.get("physical_instances"),
    control_step.get("effective_step"),
) != (1, 1, 1):
    raise SystemExit("unexpected CTRL_RS_D0 step evidence: {!r}".format(control_step))
print("positive summary OK:", summary)
print("position mapping evidence OK: tile_core -> top.u_tile; NPI full paths only")
print("dynamic step inventory/report evidence OK")
PY

CUSTOM_PORT_SPEC="$TEST_ROOT/custom_port_specs.csv"
CUSTOM_PORT_CONFIG="$TEST_ROOT/custom_port_config.json"
CUSTOM_PORT_REPORT="$TEST_ROOT/custom_port_report.json"
CUSTOM_PORT_LOG="$TEST_ROOT/custom_port_check.log"

"$PYTHON_BIN" - \
  "$PROJECT_ROOT/examples/specs.csv" \
  "$PROJECT_ROOT/config/rscheck.example.json" \
  "$CUSTOM_PORT_SPEC" \
  "$CUSTOM_PORT_CONFIG" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8-sig", newline="") as stream:
    header = next(csv.reader(stream))
with open(sys.argv[2], "r", encoding="utf-8") as stream:
    config = json.load(stream)

config["module_rules"]["rs_custom"] = {
    "has_rs_cfg_en": True,
    "step_parameters": [],
    "clk_port": "clock_i",
    "rst_port": "reset_ni",
}
with open(sys.argv[3], "w", encoding="utf-8", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerow(
        [
            "CUSTOM_IF",
            "rs_custom",
            "CUSTOM_RS",
            "tile_core",
            1,
            "clk_rs",
            "rst_n",
            "intentionally_wrong_source",
            "假门控",
        ]
    )
with open(sys.argv[4], "w", encoding="utf-8") as stream:
    json.dump(config, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY

"$PYTHON_BIN" -m rscheck check \
  --excel "$CUSTOM_PORT_SPEC" \
  --config "$CUSTOM_PORT_CONFIG" \
  --inventory "$POS_INVENTORY" \
  --json-report "$CUSTOM_PORT_REPORT" \
  2>&1 | tee "$CUSTOM_PORT_LOG"
grep -Fq 'RESULT: PASS | rows=1 errors=0 warnings=0' "$CUSTOM_PORT_LOG"

"$PYTHON_BIN" - "$CUSTOM_PORT_REPORT" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    report = json.load(stream)
row = report.get("rows", [None])[0]
if not isinstance(row, dict) or row.get("passed") is not True:
    raise SystemExit("custom-port row did not pass: {!r}".format(row))
if row.get("module_rule") != {
    "name": "rs_custom",
    "has_rs_cfg_en": True,
    "step_parameters": [],
    "clk_port": "clock_i",
    "rst_port": "reset_ni",
}:
    raise SystemExit("custom-port module rule was not retained: {!r}".format(row))
if row.get("spec", {}).get("CRG_source") != "intentionally_wrong_source":
    raise SystemExit("CRG_source report evidence was not retained: {!r}".format(row))
instances = row.get("matched_instances", [])
if len(instances) != 1 or instances[0].get("name") != "CUSTOM_RS":
    raise SystemExit("custom-port full-instance match failed: {!r}".format(instances))
ports = instances[0].get("ports", {})
if set(ports) != {"clock_i", "reset_ni", "d", "q"}:
    raise SystemExit("custom formal ports are incomplete: {!r}".format(ports))
codes = {
    finding.get("code")
    for finding in row.get("findings", [])
    if isinstance(finding, dict)
}
if any(code.startswith("CRG_SOURCE_") for code in codes if isinstance(code, str)):
    raise SystemExit("CRG_source unexpectedly affected the result: {!r}".format(codes))
print("custom module clk/rst formal-port rule evidence OK: clock_i/reset_ni")
print("CRG_source evidence retained without PASS/FAIL validation")
PY

CLK_ONLY_SPEC="$TEST_ROOT/clk_present_rst_missing_specs.csv"
CLK_ONLY_CONFIG="$TEST_ROOT/clk_present_rst_missing_config.json"
CLK_ONLY_REPORT="$TEST_ROOT/clk_present_rst_missing_report.json"
CLK_ONLY_LOG="$TEST_ROOT/clk_present_rst_missing_check.log"

"$PYTHON_BIN" - \
  "$PROJECT_ROOT/examples/specs.csv" \
  "$PROJECT_ROOT/config/rscheck.example.json" \
  "$CLK_ONLY_SPEC" \
  "$CLK_ONLY_CONFIG" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8-sig", newline="") as stream:
    header = next(csv.reader(stream))
with open(sys.argv[2], "r", encoding="utf-8") as stream:
    config = json.load(stream)

config["module_rules"]["rs_clk_only"] = {
    "has_rs_cfg_en": True,
    "step_parameters": [],
    "clk_port": "clk",
    "rst_port": "rst_n",
}
with open(sys.argv[3], "w", encoding="utf-8", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerow(
        [
            "CLK_ONLY_IF",
            "rs_clk_only",
            "CLK_ONLY_RS",
            "tile_core",
            1,
            "clk_rs",
            "rst_n",
            "not_checked",
            "假门控",
        ]
    )
with open(sys.argv[4], "w", encoding="utf-8") as stream:
    json.dump(config, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY

if "$PYTHON_BIN" -m rscheck check \
  --excel "$CLK_ONLY_SPEC" \
  --config "$CLK_ONLY_CONFIG" \
  --inventory "$POS_INVENTORY" \
  --json-report "$CLK_ONLY_REPORT" \
  2>&1 | tee "$CLK_ONLY_LOG"; then
  CLK_ONLY_CHECK_RC=0
else
  CLK_ONLY_CHECK_RC=$?
fi
[ "$CLK_ONLY_CHECK_RC" -eq 1 ] ||
  fail "clk-present/rst-missing CLI must exit 1, got $CLK_ONLY_CHECK_RC"
grep -Fq 'RESULT: FAIL | rows=1 errors=1 warnings=0' "$CLK_ONLY_LOG"
grep -Fq 'RST_PORT_MISSING' "$CLK_ONLY_LOG"
if grep -Fq 'CLK_PORT_MISSING' "$CLK_ONLY_LOG"; then
  fail "existing clk was incorrectly reported as missing"
fi

"$PYTHON_BIN" - "$CLK_ONLY_REPORT" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    report = json.load(stream)
expected_summary = {
    "passed": False,
    "rows": 1,
    "passed_rows": 0,
    "failed_rows": 1,
    "errors": 1,
    "warnings": 0,
}
if report.get("summary") != expected_summary:
    raise SystemExit("unexpected clk-present summary: {!r}".format(report.get("summary")))
row = report.get("rows", [None])[0]
if not isinstance(row, dict) or row.get("passed") is not False:
    raise SystemExit("clk-present/rst-missing row did not fail: {!r}".format(row))
if row.get("module_rule") != {
    "name": "rs_clk_only",
    "has_rs_cfg_en": True,
    "step_parameters": [],
    "clk_port": "clk",
    "rst_port": "rst_n",
}:
    raise SystemExit("clk-only module rule mismatch: {!r}".format(row.get("module_rule")))
findings = row.get("findings", [])
codes = [item.get("code") for item in findings if isinstance(item, dict)]
if codes != ["RST_PORT_MISSING"]:
    raise SystemExit("expected only RST_PORT_MISSING, got {!r}".format(codes))
instances = row.get("matched_instances", [])
if len(instances) != 1 or instances[0].get("name") != "CLK_ONLY_RS":
    raise SystemExit("clk-only instance match failed: {!r}".format(instances))
ports = instances[0].get("ports", {})
if set(ports) != {"clk", "d", "q"}:
    raise SystemExit("clk-only formal ports mismatch: {!r}".format(ports))
if ports.get("clk", {}).get("connection") != "top.u_tile.clk_rs":
    raise SystemExit("clk high connection is missing or wrong: {!r}".format(ports.get("clk")))
if instances[0].get("parameters", {}).get("RS_CRG_EN") != "0":
    raise SystemExit("clk-only RS_CRG_EN evidence is missing: {!r}".format(instances[0]))
step_check = row.get("step_check", {})
if (
    step_check.get("expected"),
    step_check.get("physical_instances"),
    step_check.get("effective_step"),
) != (1, 1, 1):
    raise SystemExit("clk-only step evidence mismatch: {!r}".format(step_check))
print("clk-present/rst-missing CLI evidence OK: ports=clk,d,q")
print("finding isolation OK: RST_PORT_MISSING only; CLK_PORT_MISSING absent")
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
grep -Fq 'contract=elab-only' "$ONLINE_GUI_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$ONLINE_GUI_LOG"
grep -Fq \
  'position-map=tile_core->top.u_tile npi-positions=full-path-only' \
  "$ONLINE_GUI_LOG"
assert_no_collector_errors "$ONLINE_GUI_LOG"

CUSTOM_PORT_GUI_LOG="$TEST_ROOT/online_gui_custom_port.log"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/test_rscheck_gui_smoke.py" \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout "$NPI_TIMEOUT" \
  --custom-port \
  --iterations 1 \
  --visible-tab results \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$CUSTOM_PORT_GUI_LOG"
grep -Fq \
  'state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=online case=custom-port iterations=1' \
  "$CUSTOM_PORT_GUI_LOG"
grep -Fq 'contract=elab-only' "$CUSTOM_PORT_GUI_LOG"
grep -Fq 'rule-ports=clock_i/reset_ni' "$CUSTOM_PORT_GUI_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$CUSTOM_PORT_GUI_LOG"
assert_no_collector_errors "$CUSTOM_PORT_GUI_LOG"

CLK_WITHOUT_RST_GUI_LOG="$TEST_ROOT/online_gui_clk_present_rst_missing.log"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/test_rscheck_gui_smoke.py" \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout "$NPI_TIMEOUT" \
  --clk-without-rst \
  --iterations "$GUI_CLK_WITHOUT_RST_ITERATIONS" \
  --visible-tab results \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$CLK_WITHOUT_RST_GUI_LOG"
grep -Fq \
  "state=FAIL rows=行数 1 errors=错误 1 warnings=警告 0 mode=online case=clk-present-rst-missing iterations=$GUI_CLK_WITHOUT_RST_ITERATIONS" \
  "$CLK_WITHOUT_RST_GUI_LOG"
grep -Fq 'contract=elab-only' "$CLK_WITHOUT_RST_GUI_LOG"
grep -Fq 'rule-ports=clk/rst_n' "$CLK_WITHOUT_RST_GUI_LOG"
grep -Fq \
  'clk-port-evidence=present rst-port-evidence=missing finding-codes=RST_PORT_MISSING' \
  "$CLK_WITHOUT_RST_GUI_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$CLK_WITHOUT_RST_GUI_LOG"
if grep -Fq 'CLK_PORT_MISSING' "$CLK_WITHOUT_RST_GUI_LOG"; then
  fail "online GUI incorrectly reported the existing clk as missing"
fi
assert_no_collector_errors "$CLK_WITHOUT_RST_GUI_LOG"

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
grep -Fq 'contract=elab-only' "$ONLINE_NEGATIVE_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$ONLINE_NEGATIVE_LOG"
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
  --visible-tab positions \
  --visible-seconds "$GUI_VISIBLE_SECONDS" \
  2>&1 | tee "$OFFLINE_STRESS_LOG"
grep -Fq \
  "state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=$GUI_STRESS_ITERATIONS" \
  "$OFFLINE_STRESS_LOG"
grep -Fq 'schemas=report-v3/inventory-v2' "$OFFLINE_STRESS_LOG"
grep -Fq \
  'position-map=tile_core->top.u_tile npi-positions=full-path-only' \
  "$OFFLINE_STRESS_LOG"

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

for gui_log in \
  "$ONLINE_GUI_LOG" \
  "$CUSTOM_PORT_GUI_LOG" \
  "$CLK_WITHOUT_RST_GUI_LOG" \
  "$PARTIAL_GUI_LOG" \
  "$ONLINE_NEGATIVE_LOG" \
  "$DEFAULT_RULE_LOG" \
  "$OFFLINE_STRESS_LOG" \
  "$OFFLINE_LOAD_LOG"; do
  grep -Fq 'header-map=column-index strict-header=false' "$gui_log"
  grep -Fq \
    'config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules' \
    "$gui_log"
done

assert_no_unexpected_collector_logs
assert_verdi_still_ready

trap - ERR
echo "PASS: partial KDB compatibility, arbitrary Excel headers, position mapping, fresh KDB online GUI checks, and offline GUI stress suite completed."
echo "ELAB_DB=$ELAB_DB"
echo "PARTIAL_ELAB_DB=$PARTIAL_ELAB_DB"
echo "PARTIAL_REPORT=$PARTIAL_REPORT"
echo "REPORT=$POS_REPORT"
echo "VERDI_LOG=$VERDI_LOG"
echo "GUI_LOGS=$PARTIAL_GUI_LOG,$ONLINE_GUI_LOG,$CUSTOM_PORT_GUI_LOG,$CLK_WITHOUT_RST_GUI_LOG,$ONLINE_NEGATIVE_LOG,$DEFAULT_RULE_LOG,$OFFLINE_STRESS_LOG,$OFFLINE_LOAD_LOG"
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
