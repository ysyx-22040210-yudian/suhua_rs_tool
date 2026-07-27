#!/usr/bin/env bash

# Focused VM pressure test for the patched kdebug rscheck.inventory backend.
{ set +x; } 2>/dev/null
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
KDEBUG_BIN="${KDEBUG_BIN:-}"
CLEAN_ELAB_DB="${CLEAN_ELAB_DB:-}"
PARTIAL_ELAB_DB="${PARTIAL_ELAB_DB:-}"
OUTPUT_BASE="${OUTPUT_BASE:-$PROJECT_ROOT/output}"
PRESSURE_ITERATIONS="${PRESSURE_ITERATIONS:-20}"
ACTION_TIMEOUT_SECONDS="${ACTION_TIMEOUT_SECONDS:-60}"
VERDI_LICENSE_USER="${VERDI_LICENSE_USER:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

for value in "$PRESSURE_ITERATIONS" "$ACTION_TIMEOUT_SECONDS"; do
  case "$value" in
    ''|*[!0-9]*) fail "pressure iterations and timeout must be ASCII integers" ;;
  esac
done
[ "$PRESSURE_ITERATIONS" -gt 0 ] || fail "PRESSURE_ITERATIONS must be positive"
[ "$ACTION_TIMEOUT_SECONDS" -ge 10 ] || fail "ACTION_TIMEOUT_SECONDS must be at least 10"

for command_name in "$PYTHON_BIN" timeout mktemp sha256sum ldd find sort comm sleep grep tee; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command not found: $command_name"
done

if [ -z "${LM_LICENSE_FILE:-}" ] && [ -z "${SNPSLMD_LICENSE_FILE:-}" ] &&
   [ -n "$VERDI_LICENSE_USER" ]; then
  [ "$(id -u)" -eq 0 ] || fail "VERDI_LICENSE_USER requires root"
  case "$VERDI_LICENSE_USER" in
    -*|''|*[!A-Za-z0-9_.-]*) fail "VERDI_LICENSE_USER is invalid" ;;
  esac
  getent passwd "$VERDI_LICENSE_USER" >/dev/null ||
    fail "VERDI_LICENSE_USER does not exist"
  while IFS='=' read -r name value; do
    case "$name" in
      LM_LICENSE_FILE|SNPSLMD_LICENSE_FILE)
        [ -n "$value" ] && export "$name=$value"
        ;;
    esac
  done < <(su - "$VERDI_LICENSE_USER" -c env)
fi
[ -n "${LM_LICENSE_FILE:-}" ] || [ -n "${SNPSLMD_LICENSE_FILE:-}" ] ||
  fail "Verdi license environment is empty"

case "$KDEBUG_BIN" in
  /*) ;;
  *) fail "KDEBUG_BIN must be an absolute path" ;;
esac
case "$CLEAN_ELAB_DB" in
  /*) ;;
  *) fail "CLEAN_ELAB_DB must be an absolute path" ;;
esac
case "$PARTIAL_ELAB_DB" in
  /*) ;;
  *) fail "PARTIAL_ELAB_DB must be an absolute path" ;;
esac
[ -x "$KDEBUG_BIN" ] || fail "kdebug executable not found: $KDEBUG_BIN"
[ -d "$CLEAN_ELAB_DB" ] || fail "clean elaborated KDB not found: $CLEAN_ELAB_DB"
[ -d "$PARTIAL_ELAB_DB" ] || fail "partial elaborated KDB not found: $PARTIAL_ELAB_DB"
[ "$(basename "$CLEAN_ELAB_DB")" != work.lib++ ] || fail "clean input is not elaborated"
[ "$(basename "$PARTIAL_ELAB_DB")" != work.lib++ ] || fail "partial input is not elaborated"

KDEBUG_BIN="$(cd "$(dirname "$KDEBUG_BIN")" && pwd -P)/$(basename "$KDEBUG_BIN")"
KDEBUG_BUILD_DIR="$(dirname "$KDEBUG_BIN")"
KDEBUG_ENGINE="$KDEBUG_BUILD_DIR/libexec/kdebug-engine"
KDEBUG_ENGINE_PY="$KDEBUG_BUILD_DIR/libexec/tcl_engine/kdebug_engine.py"
KDEBUG_NPI_TCL="$KDEBUG_BUILD_DIR/libexec/tcl_engine/kdebug_npi.tcl"
KDEBUG_RSCHECK_TCL="$KDEBUG_BUILD_DIR/libexec/tcl_engine/rscheck_inventory.tcl"
COLLECTOR="$PROJECT_ROOT/scripts/rs_kdebug_collector.py"
for path in \
  "$KDEBUG_ENGINE" \
  "$KDEBUG_ENGINE_PY" \
  "$KDEBUG_NPI_TCL" \
  "$KDEBUG_RSCHECK_TCL" \
  "$COLLECTOR"; do
  [ -f "$path" ] || fail "required backend file not found: $path"
done
[ -x "$KDEBUG_ENGINE" ] || fail "kdebug engine is not executable"

if ldd "$KDEBUG_BIN" | grep -Eiq 'libNPI|libnpiL1|not found'; then
  fail "kdebug frontend has a direct NPI or unresolved library dependency"
fi

mkdir -p "$OUTPUT_BASE"
RUN_ROOT="$(mktemp -d "$OUTPUT_BASE/kdebug_pressure.XXXXXXXX")"
export TMPDIR="$RUN_ROOT/tmp"
export KDEBUG_HOME="$RUN_ROOT/kdebug_home"
export RSCHECK_KDEBUG_HOME="$KDEBUG_HOME"
export PYTHON="$(command -v "$PYTHON_BIN")"
mkdir -p "$TMPDIR" "$KDEBUG_HOME"

finish() {
  local rc=$?
  trap - EXIT
  echo "kdebug pressure exit code: $rc"
  echo "RUN_ROOT=$RUN_ROOT"
  echo "MANIFEST=$RUN_ROOT/build_manifest.txt"
  exit "$rc"
}
trap finish EXIT

POSITIONS="$RUN_ROOT/positions.txt"
TRACE_RULES="$RUN_ROOT/trace_rules.tsv"
REQUEST="$RUN_ROOT/clean_request.json"
PARTIAL_REQUEST="$RUN_ROOT/partial_request.json"
printf 'top.u_tile\ntop.u_tile_peer\n' >"$POSITIONS"
printf 'rs_clk_only\tclk\nrs_custom\tclock_i\nrs_pipe\tclk\n' >"$TRACE_RULES"

REQUEST_TIMEOUT_MS=$(((ACTION_TIMEOUT_SECONDS - 5) * 1000))
"$PYTHON_BIN" - "$CLEAN_ELAB_DB" "$REQUEST" "$REQUEST_TIMEOUT_MS" <<'PY'
import json
import sys

request = {
    "api_version": "kdebug.v1",
    "action": "rscheck.inventory",
    "target": {"elab_db": sys.argv[1]},
    "args": {
        "positions": ["top.u_tile", "top.u_tile_peer"],
        "trace_rules": {
            "rs_clk_only": "clk",
            "rs_custom": "clock_i",
            "rs_pipe": "clk",
        },
        "trace_max_depth": 16,
        "clk_port": "clk",
        "rst_port": "rst_n",
    },
    "output": {"format": "json"},
    "limits": {"timeout_ms": int(sys.argv[3])},
}
with open(sys.argv[2], "w", encoding="utf-8") as stream:
    json.dump(request, stream, separators=(",", ":"))
    stream.write("\n")
PY

"$PYTHON_BIN" - "$REQUEST" "$PARTIAL_ELAB_DB" "$PARTIAL_REQUEST" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    request = json.load(stream)
request["target"]["elab_db"] = sys.argv[2]
with open(sys.argv[3], "w", encoding="utf-8") as stream:
    json.dump(request, stream, separators=(",", ":"))
    stream.write("\n")
PY

backend_pids() {
  local cmdline
  local argument
  local pid
  for cmdline in /proc/[0-9]*/cmdline; do
    [ -r "$cmdline" ] || continue
    pid="${cmdline#/proc/}"
    pid="${pid%/cmdline}"
    while IFS= read -r -d '' argument; do
      case "$argument" in
        "$KDEBUG_BIN"|"$KDEBUG_ENGINE"|"$KDEBUG_ENGINE_PY"|"$KDEBUG_NPI_TCL")
          printf '%s\n' "$pid"
          break
          ;;
      esac
    done <"$cmdline"
  done
}

verdi_pids_for_tmpdir() {
  local expected_tmp=$1
  local cmdline
  local environ
  local argument
  local variable
  local pid
  local has_npi_tcl
  local has_response_path
  for cmdline in /proc/[0-9]*/cmdline; do
    [ -r "$cmdline" ] || continue
    pid="${cmdline#/proc/}"
    pid="${pid%/cmdline}"
    environ="/proc/$pid/environ"
    [ -r "$environ" ] || continue
    has_npi_tcl=0
    while IFS= read -r -d '' argument; do
      if [ "$argument" = "$KDEBUG_NPI_TCL" ]; then
        has_npi_tcl=1
        break
      fi
    done <"$cmdline"
    [ "$has_npi_tcl" -eq 1 ] || continue
    has_response_path=0
    while IFS= read -r -d '' variable; do
      case "$variable" in
        KDEBUG_TCL_RESPONSE_JSON="$expected_tmp"/*)
          has_response_path=1
          break
          ;;
      esac
    done <"$environ"
    [ "$has_response_path" -eq 1 ] && printf '%s\n' "$pid"
  done
}

BASELINE_PIDS="$RUN_ROOT/backend_pids.before"
FINAL_PIDS="$RUN_ROOT/backend_pids.after"
NEW_PIDS="$RUN_ROOT/backend_pids.new"
backend_pids | sort -u >"$BASELINE_PIDS"

run_action() {
  local request=$1
  local response=$2
  local stderr_path=$3
  timeout --signal=TERM --kill-after=3 "$ACTION_TIMEOUT_SECONDS" \
    "$KDEBUG_BIN" --json - <"$request" >"$response" 2>"$stderr_path"
}

run_adapter() {
  local elab_db=$1
  local output=$2
  local stdout_path=$3
  local stderr_path=$4
  timeout --signal=TERM --kill-after=3 "$ACTION_TIMEOUT_SECONDS" \
    "$PYTHON_BIN" "$COLLECTOR" \
      --positions "$POSITIONS" \
      --output "$output" \
      --trace-rules "$TRACE_RULES" \
      --trace-max-depth 16 \
      --clk-port clk \
      --rst-port rst_n \
      --elab-db "$elab_db" \
      >"$stdout_path" 2>"$stderr_path"
}

validate_response() {
  local path=$1
  "$PYTHON_BIN" - "$path" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    response = json.load(stream)
assert response.get("api_version") == "kdebug.v1"
assert response.get("action") == "rscheck.inventory"
assert response.get("ok") is True
inventory = response["data"]["inventory"]
assert inventory.get("schema_version") == 3
assert set(inventory.get("positions", {})) == {"top.u_tile", "top.u_tile_peer"}
for name, position in inventory["positions"].items():
    assert position.get("found") is True
    instances = position.get("instances", [])
    assert len(instances) == 13, (name, len(instances))
    clk_only = next(item for item in instances if item.get("name") == "CLK_ONLY_RS")
    assert set(clk_only.get("ports", {})) == {"clk", "d", "q"}
    assert clk_only["ports"]["clk"]["connection"].endswith(".clk_rs")
print("response PASS positions=2 instances=26 schema=v3")
PY
}

validate_partial_response() {
  local path=$1
  validate_response "$path"
  "$PYTHON_BIN" - "$path" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    response = json.load(stream)
notices = response["data"]["inventory"].get("notices", [])
assert len(notices) == 1 and "NPI_LOAD_PARTIAL" in notices[0], notices
print("raw partial PASS notice=1")
PY
}

validate_inventory_pair() {
  local clean=$1
  local partial=$2
  local partial_stderr=$3
  "$PYTHON_BIN" - "$clean" "$partial" "$partial_stderr" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    clean = json.load(stream)
with open(sys.argv[2], "r", encoding="utf-8") as stream:
    partial = json.load(stream)
assert clean.get("schema_version") == partial.get("schema_version") == 3
expected = {"top.u_tile", "top.u_tile_peer"}
assert set(clean.get("positions", {})) == set(partial.get("positions", {})) == expected
assert clean.get("notices", []) == []
notices = partial.get("notices", [])
assert len(notices) == 1 and "NPI_LOAD_PARTIAL" in notices[0]
assert "warning[NPI_LOAD_PARTIAL]" in open(
    sys.argv[3], "r", encoding="utf-8", errors="replace"
).read()
for inventory in (clean, partial):
    assert sum(len(item["instances"]) for item in inventory["positions"].values()) == 26
print("adapter PASS clean+partial schema=v3 partial_notice=1")
PY
}

RAW_RESPONSE="$RUN_ROOT/raw_clean_response.json"
run_action "$REQUEST" "$RAW_RESPONSE" "$RUN_ROOT/raw_clean.stderr"
validate_response "$RAW_RESPONSE" | tee "$RUN_ROOT/raw_clean.validation.log"
RAW_PARTIAL_RESPONSE="$RUN_ROOT/raw_partial_response.json"
run_action \
  "$PARTIAL_REQUEST" "$RAW_PARTIAL_RESPONSE" "$RUN_ROOT/raw_partial.stderr"
validate_partial_response "$RAW_PARTIAL_RESPONSE" \
  | tee "$RUN_ROOT/raw_partial.validation.log"

ADAPTER_CLEAN="$RUN_ROOT/adapter_clean_inventory.json"
ADAPTER_PARTIAL="$RUN_ROOT/adapter_partial_inventory.json"
export KDEBUG_BIN
export RSCHECK_COLLECTOR_TIMEOUT_SECONDS="$ACTION_TIMEOUT_SECONDS"
run_adapter \
  "$CLEAN_ELAB_DB" "$ADAPTER_CLEAN" \
  "$RUN_ROOT/adapter_clean.stdout" "$RUN_ROOT/adapter_clean.stderr"
run_adapter \
  "$PARTIAL_ELAB_DB" "$ADAPTER_PARTIAL" \
  "$RUN_ROOT/adapter_partial.stdout" "$RUN_ROOT/adapter_partial.stderr"
validate_inventory_pair \
  "$ADAPTER_CLEAN" "$ADAPTER_PARTIAL" "$RUN_ROOT/adapter_partial.stderr" \
  | tee "$RUN_ROOT/adapter.validation.log"

PRESSURE_LOG="$RUN_ROOT/pressure.log"
iteration=1
while [ "$iteration" -le "$PRESSURE_ITERATIONS" ]; do
  response="$RUN_ROOT/pressure.$iteration.json"
  run_action "$REQUEST" "$response" "$RUN_ROOT/pressure.$iteration.stderr"
  validate_response "$response" >>"$PRESSURE_LOG"
  printf 'round=%s/%s PASS\n' "$iteration" "$PRESSURE_ITERATIONS" \
    | tee -a "$PRESSURE_LOG"
  iteration=$((iteration + 1))
done

if find "$TMPDIR" -maxdepth 1 -type d -name 'kdebug-tcl-npi-*' | grep -q .; then
  find "$TMPDIR" -maxdepth 1 -type d -name 'kdebug-tcl-npi-*' >&2
  fail "kdebug Tcl/Verdi temporary directories leaked after pressure"
fi

CANCEL_HOME="$RUN_ROOT/cancel_home"
CANCEL_TMP="$RUN_ROOT/cancel_tmp"
mkdir -p "$CANCEL_HOME" "$CANCEL_TMP"
KDEBUG_HOME="$CANCEL_HOME" TMPDIR="$CANCEL_TMP" \
  "$KDEBUG_BIN" --json - <"$REQUEST" \
  >"$RUN_ROOT/cancel.stdout" 2>"$RUN_ROOT/cancel.stderr" &
CANCEL_PID=$!
CANCEL_STARTED=0
CANCEL_VERDI_PIDS="$RUN_ROOT/cancel_verdi_pids.started"
for _ in {1..100}; do
  verdi_pids_for_tmpdir "$CANCEL_TMP" >"$CANCEL_VERDI_PIDS"
  if [ -s "$CANCEL_VERDI_PIDS" ]; then
    CANCEL_STARTED=1
    break
  fi
  kill -0 "$CANCEL_PID" 2>/dev/null || break
  sleep 0.05
done
[ "$CANCEL_STARTED" -eq 1 ] || fail "frontend cancellation did not reach a live Verdi NPI action"
kill -TERM "$CANCEL_PID"
set +e
wait "$CANCEL_PID"
CANCEL_RC=$?
set -e
[ "$CANCEL_RC" -ne 0 ] || fail "externally terminated kdebug frontend returned success"
for _ in {1..40}; do
  if ! find "$CANCEL_TMP" -maxdepth 1 -type d -name 'kdebug-tcl-npi-*' | grep -q .; then
    break
  fi
  sleep 0.05
done
if find "$CANCEL_TMP" -maxdepth 1 -type d -name 'kdebug-tcl-npi-*' | grep -q .; then
  find "$CANCEL_TMP" -maxdepth 1 -type d -name 'kdebug-tcl-npi-*' >&2
  fail "frontend cancellation left a kdebug Tcl/Verdi temporary directory"
fi

TIMEOUT_HOME="$RUN_ROOT/timeout_home"
TIMEOUT_OUTPUT="$RUN_ROOT/timeout_inventory.json"
mkdir -p "$TIMEOUT_HOME"
set +e
RSCHECK_KDEBUG_HOME="$TIMEOUT_HOME" \
RSCHECK_COLLECTOR_TIMEOUT_SECONDS=1 \
timeout --signal=TERM --kill-after=3 5 \
  "$PYTHON_BIN" "$COLLECTOR" \
    --positions "$POSITIONS" \
    --output "$TIMEOUT_OUTPUT" \
    --trace-rules "$TRACE_RULES" \
    --trace-max-depth 16 \
    --clk-port clk \
    --rst-port rst_n \
    --elab-db "$CLEAN_ELAB_DB" \
    >"$RUN_ROOT/timeout.stdout" 2>"$RUN_ROOT/timeout.stderr"
TIMEOUT_RC=$?
set -e
[ "$TIMEOUT_RC" -ne 0 ] || fail "one-second timeout unexpectedly completed"
[ ! -e "$TIMEOUT_OUTPUT" ] || fail "timed-out adapter produced a success inventory"
grep -Eq \
  'error\[KDEBUG_TIMEOUT\]|error\[KDEBUG_ACTION\]: INTERNAL_ENGINE_FAILED: internal engine timed out' \
  "$RUN_ROOT/timeout.stderr" ||
  fail "timed-out adapter did not report a bounded kdebug timeout"

if find "$TMPDIR" -maxdepth 1 -type d -name 'kdebug-tcl-npi-*' | grep -q .; then
  find "$TMPDIR" -maxdepth 1 -type d -name 'kdebug-tcl-npi-*' >&2
  fail "kdebug Tcl/Verdi temporary directories leaked after forced timeout"
fi

for _ in {1..20}; do
  backend_pids | sort -u >"$FINAL_PIDS"
  comm -13 "$BASELINE_PIDS" "$FINAL_PIDS" >"$NEW_PIDS"
  [ ! -s "$NEW_PIDS" ] && break
  sleep 0.1
done
[ ! -s "$NEW_PIDS" ] || fail "kdebug, engine, or Verdi child process leaked"

"$PYTHON_BIN" - "$KDEBUG_HOME" "$TIMEOUT_HOME" "$CANCEL_HOME" <<'PY'
import json
import pathlib
import sys

roots = [pathlib.Path(value) for value in sys.argv[1:]]
crashes = [
    str(path)
    for root in roots
    for path in root.rglob("crash_marker*")
    if path.stat().st_size
]
assert not crashes, crashes
active = []
for root in roots:
    for path in root.rglob("registry.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and document.get("sessions"):
            active.append((str(path), document["sessions"]))
assert not active, active
print("lifecycle PASS cancellation+timeout left no crash marker, active session, or process leak")
PY

{
  KVERIF_REPO_ROOT="$(cd "$KDEBUG_BUILD_DIR/.." && pwd -P)"
  KVERIF_COMMIT="$(cd "$KVERIF_REPO_ROOT" && git rev-parse HEAD 2>/dev/null || true)"
  RSCHECK_COMMIT="$(cd "$PROJECT_ROOT" && git rev-parse HEAD 2>/dev/null || true)"
  printf 'project_root=%s\n' "$PROJECT_ROOT"
  printf 'rscheck_commit=%s\n' "${RSCHECK_COMMIT:-unavailable}"
  printf 'kverif_repo=%s\n' "$KVERIF_REPO_ROOT"
  printf 'kverif_commit=%s\n' "${KVERIF_COMMIT:-unavailable}"
  printf 'kdebug_bin=%s\n' "$KDEBUG_BIN"
  printf 'clean_elab_db=%s\n' "$CLEAN_ELAB_DB"
  printf 'partial_elab_db=%s\n' "$PARTIAL_ELAB_DB"
  printf 'pressure_iterations=%s\n' "$PRESSURE_ITERATIONS"
  "$PYTHON_BIN" --version
  sha256sum \
    "$KDEBUG_BIN" \
    "$KDEBUG_ENGINE" \
    "$KDEBUG_ENGINE_PY" \
    "$KDEBUG_NPI_TCL" \
    "$KDEBUG_RSCHECK_TCL" \
    "$PROJECT_ROOT/rscheck/kdebug_collector.py" \
    "$COLLECTOR"
} >"$RUN_ROOT/build_manifest.txt"

echo "PASS: kdebug raw/adapter clean+partial, $PRESSURE_ITERATIONS pressure rounds, frontend cancellation, and timeout cleanup completed."
