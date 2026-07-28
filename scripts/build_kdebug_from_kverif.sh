#!/usr/bin/env bash

# Download the pinned kverif source and build a relocatable kdebug runtime tree.
{ set +x; } 2>/dev/null
set -Eeuo pipefail
umask 077

readonly KVERIF_REPO_URL="https://github.com/ysyx-22040210-yudian/kverif.git"
readonly KVERIF_BRANCH="codex/rscheck-elab-inventory"
readonly KVERIF_COMMIT="2b43b799c8f7f8586a9e6c2128335e74d971e633"
KVERIF_MIRROR_URL="${KVERIF_MIRROR_URL:-$KVERIF_REPO_URL}"
OUTPUT_BASE="${OUTPUT_BASE:-$PWD/output}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CXX_BIN="${CXX_BIN:-g++}"
BUILD_JOBS="${BUILD_JOBS:-2}"
CLONE_TIMEOUT_SECONDS="${CLONE_TIMEOUT_SECONDS:-1800}"
SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-300}"
SMOKE_ELAB_DB=""
SMOKE_POSITION=""

usage() {
  cat <<'EOF'
Usage: bash scripts/build_kdebug_from_kverif.sh [options]

Download the pinned kverif commit, compile and test kdebug, validate the ELF,
and create a relocatable runtime archive containing the compiled frontend, its
private libexec tree, provenance, checksums, and the kverif license.

Options:
  --output-base DIR       Absolute parent directory for the unique build run.
  --python COMMAND        Python with pytest/jsonschema for build/tests.
  --cxx COMMAND           C++ compiler executable (default: g++).
  --jobs N                Parallel make jobs (default: 2).
  --clone-timeout SEC     Git clone/fetch timeout (default: 1800).
  --smoke-elab-db DIR     Optional absolute Verdi *.elab++ KDB for live smoke.
  --smoke-position PATH   Full RTL position used with --smoke-elab-db.
  --smoke-timeout SEC     Live inventory timeout (default: 300).
  --help                  Show this help.

Environment settings:
  OUTPUT_BASE, PYTHON_BIN, CXX_BIN, BUILD_JOBS, CLONE_TIMEOUT_SECONDS,
  SMOKE_TIMEOUT_SECONDS. KVERIF_MIRROR_URL may point to a trusted mirror that
  contains the pinned commit; the canonical repository, branch, and commit
  cannot be overridden.

The rscheck process executes only the generated ELF as:
  <runtime-dir>/kdebug --json -

The adjacent libexec files are private runtime resources used by kdebug after
the ELF starts. They are not valid KDEBUG_BIN values and rscheck never executes
their Tcl files directly.
EOF
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-base)
      [ "$#" -ge 2 ] || fail "--output-base requires a value"
      OUTPUT_BASE=$2
      shift 2
      ;;
    --python)
      [ "$#" -ge 2 ] || fail "--python requires a value"
      PYTHON_BIN=$2
      shift 2
      ;;
    --cxx)
      [ "$#" -ge 2 ] || fail "--cxx requires a value"
      CXX_BIN=$2
      shift 2
      ;;
    --jobs)
      [ "$#" -ge 2 ] || fail "--jobs requires a value"
      BUILD_JOBS=$2
      shift 2
      ;;
    --clone-timeout)
      [ "$#" -ge 2 ] || fail "--clone-timeout requires a value"
      CLONE_TIMEOUT_SECONDS=$2
      shift 2
      ;;
    --smoke-elab-db)
      [ "$#" -ge 2 ] || fail "--smoke-elab-db requires a value"
      SMOKE_ELAB_DB=$2
      shift 2
      ;;
    --smoke-position)
      [ "$#" -ge 2 ] || fail "--smoke-position requires a value"
      SMOKE_POSITION=$2
      shift 2
      ;;
    --smoke-timeout)
      [ "$#" -ge 2 ] || fail "--smoke-timeout requires a value"
      SMOKE_TIMEOUT_SECONDS=$2
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

case "$OUTPUT_BASE" in
  /*) ;;
  *) fail "OUTPUT_BASE must be an absolute path" ;;
esac
[ -n "$KVERIF_MIRROR_URL" ] || fail "KVERIF_MIRROR_URL cannot be empty"
case "$KVERIF_MIRROR_URL" in
  -*) fail "KVERIF_MIRROR_URL cannot begin with '-'" ;;
esac
case "$BUILD_JOBS" in
  ''|*[!0-9]*) fail "BUILD_JOBS must be an ASCII integer" ;;
esac
case "$CLONE_TIMEOUT_SECONDS" in
  ''|*[!0-9]*) fail "CLONE_TIMEOUT_SECONDS must be an ASCII integer" ;;
esac
case "$SMOKE_TIMEOUT_SECONDS" in
  ''|*[!0-9]*) fail "SMOKE_TIMEOUT_SECONDS must be an ASCII integer" ;;
esac
[ "$BUILD_JOBS" -gt 0 ] || fail "BUILD_JOBS must be positive"
[ "$CLONE_TIMEOUT_SECONDS" -ge 60 ] || fail "CLONE_TIMEOUT_SECONDS must be at least 60"
[ "$SMOKE_TIMEOUT_SECONDS" -ge 30 ] || fail "SMOKE_TIMEOUT_SECONDS must be at least 30"

if { [ -n "$SMOKE_ELAB_DB" ] && [ -z "$SMOKE_POSITION" ]; } ||
   { [ -z "$SMOKE_ELAB_DB" ] && [ -n "$SMOKE_POSITION" ]; }; then
  fail "--smoke-elab-db and --smoke-position must be provided together"
fi
if [ -n "$SMOKE_ELAB_DB" ]; then
  case "$SMOKE_ELAB_DB" in
    /*) ;;
    *) fail "--smoke-elab-db must be an absolute path" ;;
  esac
  [ -d "$SMOKE_ELAB_DB" ] || fail "smoke elaborated KDB directory not found: $SMOKE_ELAB_DB"
  SMOKE_ELAB_DB="$(cd "$SMOKE_ELAB_DB" && pwd -P)"
  case "$SMOKE_ELAB_DB" in
    *.elab++) ;;
    *) fail "--smoke-elab-db must resolve to a Verdi *.elab++ directory" ;;
  esac
fi

for command_name in git make timeout mktemp file ldd od tr grep sha256sum tar gzip cp tee head uname find sort chmod; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command not found: $command_name"
done
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python executable not found: $PYTHON_BIN"
command -v "$CXX_BIN" >/dev/null 2>&1 || fail "C++ compiler executable not found: $CXX_BIN"
if ! "$PYTHON_BIN" -c 'import jsonschema, pytest' >/dev/null 2>&1; then
  fail "Python test dependencies are missing; install pytest and jsonschema for $PYTHON_BIN"
fi

mkdir -p "$OUTPUT_BASE"
OUTPUT_BASE="$(cd "$OUTPUT_BASE" && pwd -P)"
RUN_ROOT="$(mktemp -d "$OUTPUT_BASE/kverif_kdebug_build.XXXXXXXX")"
SOURCE_ROOT="$RUN_ROOT/kverif"
BUILD_LOG="$RUN_ROOT/build.log"
TEST_LOG="$RUN_ROOT/test-fast.log"

finish() {
  local rc=$?
  trap - EXIT
  echo "BUILD_EXIT_CODE=$rc"
  echo "RUN_ROOT=$RUN_ROOT"
  exit "$rc"
}
trap finish EXIT

echo "Cloning pinned kverif branch $KVERIF_BRANCH"
timeout "$CLONE_TIMEOUT_SECONDS" git clone \
  --depth 1 \
  --branch "$KVERIF_BRANCH" \
  --single-branch \
  -- "$KVERIF_MIRROR_URL" \
  "$SOURCE_ROOT"

HEAD_COMMIT="$(cd "$SOURCE_ROOT" && git rev-parse HEAD)"
if [ "$HEAD_COMMIT" != "$KVERIF_COMMIT" ]; then
  echo "Branch head is $HEAD_COMMIT; fetching pinned commit $KVERIF_COMMIT"
  (
    cd "$SOURCE_ROOT"
    timeout "$CLONE_TIMEOUT_SECONDS" git fetch --depth 1 origin "$KVERIF_COMMIT"
    git checkout --detach FETCH_HEAD
  )
  HEAD_COMMIT="$(cd "$SOURCE_ROOT" && git rev-parse HEAD)"
fi
[ "$HEAD_COMMIT" = "$KVERIF_COMMIT" ] ||
  fail "checked out kverif commit $HEAD_COMMIT, expected $KVERIF_COMMIT"
[ -z "$(cd "$SOURCE_ROOT" && git status --porcelain --untracked-files=normal)" ] ||
  fail "fresh kverif checkout is unexpectedly dirty"

KDEBUG_DIR="$SOURCE_ROOT/kdebug"
echo "Building kdebug ELF from pinned commit $HEAD_COMMIT"
PYTHON="$PYTHON_BIN" CXX="$CXX_BIN" make -C "$KDEBUG_DIR" clean >>"$BUILD_LOG" 2>&1
PYTHON="$PYTHON_BIN" CXX="$CXX_BIN" make -C "$KDEBUG_DIR" -j"$BUILD_JOBS" all >>"$BUILD_LOG" 2>&1
echo "Running the kverif kdebug fast test suite"
PYTHON="$PYTHON_BIN" CXX="$CXX_BIN" make -C "$KDEBUG_DIR" test-fast >"$TEST_LOG" 2>&1

KDEBUG_BIN="$KDEBUG_DIR/kdebug"
KDEBUG_ENGINE="$KDEBUG_DIR/libexec/kdebug-engine"
KDEBUG_ENGINE_PY="$KDEBUG_DIR/libexec/tcl_engine/kdebug_engine.py"
KDEBUG_INTERNAL_NPI_TCL="$KDEBUG_DIR/libexec/tcl_engine/kdebug_npi.tcl"
KDEBUG_INTERNAL_RSCHECK_TCL="$KDEBUG_DIR/libexec/tcl_engine/rscheck_inventory.tcl"

[ -x "$KDEBUG_BIN" ] || fail "compiled kdebug ELF was not produced: $KDEBUG_BIN"
[ -x "$KDEBUG_ENGINE" ] || fail "kdebug private engine is missing: $KDEBUG_ENGINE"
for internal_resource in \
  "$KDEBUG_ENGINE_PY" \
  "$KDEBUG_INTERNAL_NPI_TCL" \
  "$KDEBUG_INTERNAL_RSCHECK_TCL"; do
  [ -s "$internal_resource" ] || fail "kdebug private runtime resource is missing or empty: $internal_resource"
done

KDEBUG_MAGIC="$(LC_ALL=C od -An -tx1 -N4 "$KDEBUG_BIN" | tr -d '[:space:]')"
[ "$KDEBUG_MAGIC" = 7f454c46 ] || fail "compiled kdebug output is not a Linux ELF"
LC_ALL=C file "$KDEBUG_BIN" | tee "$RUN_ROOT/kdebug.file"
LC_ALL=C ldd "$KDEBUG_BIN" | tee "$RUN_ROOT/kdebug.ldd"
if LC_ALL=C grep -Eiq 'libNPI|libnpiL1|not found' "$RUN_ROOT/kdebug.ldd"; then
  fail "compiled kdebug frontend has a direct NPI or unresolved library dependency"
fi
"$KDEBUG_BIN" actions >"$RUN_ROOT/source-actions.txt"
grep -Fq 'rscheck.inventory' "$RUN_ROOT/source-actions.txt" ||
  fail "compiled kdebug does not expose rscheck.inventory"

SHORT_COMMIT="${HEAD_COMMIT:0:8}"
ARCH="$(uname -m)"
SOURCE_DATE_EPOCH="$(cd "$SOURCE_ROOT" && git show -s --format=%ct "$HEAD_COMMIT")"
PACKAGE_NAME="kdebug-runtime-$SHORT_COMMIT"
PACKAGE_DIR="$RUN_ROOT/$PACKAGE_NAME"
mkdir -p "$PACKAGE_DIR"
cp "$KDEBUG_BIN" "$PACKAGE_DIR/kdebug"
cp -a "$KDEBUG_DIR/libexec" "$PACKAGE_DIR/libexec"
cp "$SOURCE_ROOT/LICENSE" "$PACKAGE_DIR/LICENSE"
{
  printf 'kverif_repo=%s\n' "$KVERIF_REPO_URL"
  printf 'kverif_branch=%s\n' "$KVERIF_BRANCH"
  printf 'kverif_commit=%s\n' "$HEAD_COMMIT"
  printf 'architecture=%s\n' "$ARCH"
  printf 'rscheck_exec=./kdebug --json -\n'
} >"$PACKAGE_DIR/BUILD_INFO.txt"
find "$PACKAGE_DIR" -depth \
  \( -type d -name __pycache__ -o -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
  -delete
chmod -R u=rwX,go=rX "$PACKAGE_DIR"
(
  cd "$PACKAGE_DIR"
  while IFS= read -r -d '' runtime_file; do
    sha256sum "$runtime_file"
  done < <(find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z)
) >"$PACKAGE_DIR/SHA256SUMS"

PACKAGE_ARCHIVE="$RUN_ROOT/$PACKAGE_NAME-linux-$ARCH.tar.gz"
(
  cd "$RUN_ROOT"
  unset GZIP
  find "$PACKAGE_NAME" -print0 | LC_ALL=C sort -z | \
    TAR_OPTIONS= tar \
      --null \
      --no-recursion \
      --format=gnu \
      --mtime="@$SOURCE_DATE_EPOCH" \
      --owner=0 \
      --group=0 \
      --numeric-owner \
      -cf - \
      -T - | gzip -n -6 >"$PACKAGE_ARCHIVE"
)

ARCHIVE_SMOKE_ROOT="$RUN_ROOT/archive-smoke"
mkdir -p "$ARCHIVE_SMOKE_ROOT"
TAR_OPTIONS= tar -xzf "$PACKAGE_ARCHIVE" -C "$ARCHIVE_SMOKE_ROOT"
RUNTIME_DIR="$ARCHIVE_SMOKE_ROOT/$PACKAGE_NAME"
RUNTIME_KDEBUG_BIN="$RUNTIME_DIR/kdebug"
[ -x "$RUNTIME_KDEBUG_BIN" ] || fail "packaged kdebug ELF is not executable"
[ "$(LC_ALL=C od -An -tx1 -N4 "$RUNTIME_KDEBUG_BIN" | tr -d '[:space:]')" = 7f454c46 ] ||
  fail "packaged kdebug output is not a Linux ELF"
(
  cd "$RUNTIME_DIR"
  sha256sum -c SHA256SUMS >"$RUN_ROOT/package-checksums.log"
)
"$RUNTIME_KDEBUG_BIN" actions >"$RUN_ROOT/package-actions.txt"
grep -Fq 'rscheck.inventory' "$RUN_ROOT/package-actions.txt" ||
  fail "packaged kdebug does not expose rscheck.inventory"

if [ -n "$SMOKE_ELAB_DB" ]; then
  echo "Running packaged kdebug against the supplied Verdi elaborated KDB"
  SMOKE_REQUEST="$RUN_ROOT/smoke-request.json"
  SMOKE_RESPONSE="$RUN_ROOT/smoke-response.json"
  SMOKE_STDERR="$RUN_ROOT/smoke-stderr.txt"
  "$PYTHON_BIN" - "$SMOKE_ELAB_DB" "$SMOKE_POSITION" >"$SMOKE_REQUEST" <<'PY'
import json
import sys

request = {
    "api_version": "kdebug.v1",
    "action": "rscheck.inventory",
    "target": {"elab_db": sys.argv[1]},
    "args": {
        "positions": [sys.argv[2]],
        "trace_rules": {},
        "trace_max_depth": 16,
        "clk_port": "clk",
        "rst_port": "rst_n",
    },
    "output": {"format": "json"},
}
json.dump(request, sys.stdout, separators=(",", ":"))
sys.stdout.write("\n")
PY
  mkdir -p "$RUN_ROOT/smoke-home" "$RUN_ROOT/smoke-tmp"
  if ! HOME="$RUN_ROOT/smoke-home" TMPDIR="$RUN_ROOT/smoke-tmp" \
    PYTHON="$PYTHON_BIN" \
    timeout --signal=TERM --kill-after=5s "$SMOKE_TIMEOUT_SECONDS" \
      "$RUNTIME_KDEBUG_BIN" --json - \
      <"$SMOKE_REQUEST" >"$SMOKE_RESPONSE" 2>"$SMOKE_STDERR"; then
    sed -n '1,120p' "$SMOKE_STDERR" >&2
    if [ -s "$SMOKE_RESPONSE" ]; then
      "$PYTHON_BIN" - "$SMOKE_RESPONSE" >&2 <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as stream:
        response = json.load(stream)
except (OSError, ValueError) as exc:
    print("kdebug response could not be decoded: " + str(exc))
else:
    error = response.get("error") or {}
    print(
        "kdebug response error[%s]: %s"
        % (error.get("code", "UNKNOWN"), error.get("message", "no message"))
    )
PY
    fi
    fail "packaged kdebug live rscheck.inventory command failed"
  fi
  "$PYTHON_BIN" - "$SMOKE_RESPONSE" "$SMOKE_POSITION" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    response = json.load(stream)
if response.get("api_version") != "kdebug.v1":
    raise SystemExit("unexpected kdebug API version")
if response.get("action") != "rscheck.inventory" or response.get("ok") is not True:
    raise SystemExit("rscheck.inventory did not return a successful response")
inventory = response.get("data", {}).get("inventory", {})
if inventory.get("schema_version") != 3:
    raise SystemExit("rscheck.inventory did not return inventory schema v3")
position = inventory.get("positions", {}).get(sys.argv[2])
if not isinstance(position, dict):
    raise SystemExit("rscheck.inventory did not return the requested position object")
if position.get("found") is not True:
    raise SystemExit("rscheck.inventory did not resolve the requested RTL position")
instances = position.get("instances")
if not isinstance(instances, list):
    raise SystemExit("rscheck.inventory position instances must be an array")
print(
    "LIVE_SMOKE=PASS schema=v3 position=%s instances=%d"
    % (sys.argv[2], len(instances))
)
PY
fi

ARTIFACT_CHECKSUMS="$RUN_ROOT/artifact_checksums.sha256"
(
  cd "$RUN_ROOT"
  sha256sum \
    "$PACKAGE_NAME/kdebug" \
    "$PACKAGE_NAME/libexec/kdebug-engine" \
    "$PACKAGE_NAME/libexec/tcl_engine/kdebug_engine.py" \
    "$PACKAGE_NAME/libexec/tcl_engine/kdebug_npi.tcl" \
    "$PACKAGE_NAME/libexec/tcl_engine/rscheck_inventory.tcl" \
    "$(basename "$PACKAGE_ARCHIVE")"
) >"$ARTIFACT_CHECKSUMS"

MANIFEST="$RUN_ROOT/build_manifest.txt"
{
  printf 'kverif_repo=%s\n' "$KVERIF_REPO_URL"
  if [ "$KVERIF_MIRROR_URL" = "$KVERIF_REPO_URL" ]; then
    printf 'kverif_clone_source=canonical\n'
  else
    printf 'kverif_clone_source=trusted_mirror\n'
  fi
  printf 'kverif_branch=%s\n' "$KVERIF_BRANCH"
  printf 'kverif_commit=%s\n' "$HEAD_COMMIT"
  printf 'source_root=./kverif\n'
  printf 'kdebug_bin=./kverif/kdebug/kdebug\n'
  printf 'runtime_dir=./%s\n' "$PACKAGE_NAME"
  printf 'runtime_archive=./%s\n' "$(basename "$PACKAGE_ARCHIVE")"
  printf 'runtime_checksums=./%s/SHA256SUMS\n' "$PACKAGE_NAME"
  printf 'artifact_checksums=./%s\n' "$(basename "$ARTIFACT_CHECKSUMS")"
  printf 'rscheck_exec=./%s/kdebug --json -\n' "$PACKAGE_NAME"
  printf 'live_smoke=%s\n' "$([ -n "$SMOKE_ELAB_DB" ] && printf pass || printf not_requested)"
  "$PYTHON_BIN" --version 2>&1
  "$CXX_BIN" --version | head -1
} >"$MANIFEST"

echo "PASS: downloaded pinned kverif, compiled/tested the kdebug ELF, and verified its runtime package."
echo "KVERIF_COMMIT=$HEAD_COMMIT"
echo "KDEBUG_BIN=$KDEBUG_BIN"
echo "RUNTIME_KDEBUG_BIN=$PACKAGE_DIR/kdebug"
echo "RUNTIME_ARCHIVE=$PACKAGE_ARCHIVE"
echo "RUNTIME_CHECKSUMS=$PACKAGE_DIR/SHA256SUMS"
echo "ARTIFACT_CHECKSUMS=$ARTIFACT_CHECKSUMS"
echo "MANIFEST=$MANIFEST"
echo "RSCHECK_EXEC=$PACKAGE_DIR/kdebug --json -"
