#!/usr/bin/env bash

# Clone a clean GitHub checkout into VM-native storage, pin the requested
# commit, and run the repository's complete Verdi/NPI/GUI test driver.
{ set +x; } 2>/dev/null
set -Ee -o pipefail
umask 077

REPOSITORY_URL="https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git"
DEFAULT_REVISION="origin/main"
MAX_CLONE_ATTEMPTS=3
CLONE_TIMEOUT="${CLONE_TIMEOUT:-180}"
VM_RUN_BASE="${VM_RUN_BASE:-${HOME:-}}"

usage() {
  cat <<'EOF'
Usage: bash scripts/test_vm_fresh_checkout.sh [--commit REV]

Create an independent VM-native checkout and run the complete Verdi/NPI/GUI
test suite. Without --commit, the checkout is pinned to origin/main.

Options:
  --commit REV  Test the commit resolved from REV (a full commit SHA is best).
  --help        Show this help and exit.

Environment:
  CLONE_TIMEOUT   Timeout in seconds for each clone attempt (default: 180).
  VM_RUN_BASE     Absolute writable parent for the run directory (default: HOME).
  VERDI_ENV_FILE  Optional absolute site environment file, loaded in isolation.

All Verdi, NPI, GUI, compiler, timeout, and stress variables supported by
scripts/test_vm_verdi_gui.sh are inherited. Run directories are preserved.
EOF
}

usage_error() {
  echo "ERROR: $*" >&2
  usage >&2
  exit 2
}

REQUESTED_REVISION=""
COMMIT_OPTION_SEEN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help)
      [ "$#" -eq 1 ] || usage_error "--help must be used alone"
      [ "$COMMIT_OPTION_SEEN" -eq 0 ] || usage_error "--help must be used alone"
      usage
      exit 0
      ;;
    --commit)
      [ "$COMMIT_OPTION_SEEN" -eq 0 ] || usage_error "--commit may be specified only once"
      [ "$#" -ge 2 ] || usage_error "--commit requires a revision"
      case "$2" in
        ""|--*) usage_error "--commit requires a revision" ;;
      esac
      REQUESTED_REVISION="$2"
      COMMIT_OPTION_SEEN=1
      shift 2
      ;;
    *)
      usage_error "unsupported argument: $1"
      ;;
  esac
done

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "ERROR: Bash 4 or newer is required" >&2
  exit 1
fi

case "$CLONE_TIMEOUT" in
  ""|*[!0-9]*)
    echo "ERROR: CLONE_TIMEOUT must be a positive integer" >&2
    exit 1
    ;;
esac
[ "$CLONE_TIMEOUT" -gt 0 ] || {
  echo "ERROR: CLONE_TIMEOUT must be greater than zero" >&2
  exit 1
}

for required_command in git timeout mktemp mkdir tee bash; do
  command -v "$required_command" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $required_command" >&2
    exit 1
  }
done

case "$VM_RUN_BASE" in
  /*) ;;
  *)
    echo "ERROR: VM_RUN_BASE must be an absolute path" >&2
    exit 1
    ;;
esac
[ -d "$VM_RUN_BASE" ] && [ -w "$VM_RUN_BASE" ] || {
  echo "ERROR: VM_RUN_BASE must be an existing writable directory" >&2
  exit 1
}
VM_RUN_BASE="$(cd "$VM_RUN_BASE" && pwd -P)"

if [ -n "${VERDI_ENV_FILE:-}" ]; then
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
fi

RUN_ROOT="$(mktemp -d "$VM_RUN_BASE/rscheck_fresh.XXXXXXXX")"
FULL_LOG="$RUN_ROOT/full_vm_test.log"
ARTIFACT_ROOT="$RUN_ROOT/artifacts"
mkdir -p "$ARTIFACT_ROOT"

exec > >(tee -a "$FULL_LOG") 2>&1

print_locations() {
  local exit_code=$?
  trap - EXIT
  echo
  echo "Fresh-checkout test exit code: $exit_code"
  echo "RUN_ROOT=$RUN_ROOT"
  echo "FULL_LOG=$FULL_LOG"
  echo "ARTIFACT_ROOT=$ARTIFACT_ROOT"
  echo "CLONE_ATTEMPTS=$RUN_ROOT/repo_attempt*"
  exit "$exit_code"
}
trap print_locations EXIT

echo "Fresh-checkout VM test started."
echo "Requested revision: ${REQUESTED_REVISION:-$DEFAULT_REVISION}"
echo "Clone timeout per attempt: ${CLONE_TIMEOUT}s"
echo "Failed and successful clone attempts will be preserved."

REPO_ROOT=""
attempt=1
while [ "$attempt" -le "$MAX_CLONE_ATTEMPTS" ]; do
  ATTEMPT_ROOT="$RUN_ROOT/repo_attempt${attempt}"
  ATTEMPT_CHECKOUT="$ATTEMPT_ROOT/repository"
  mkdir "$ATTEMPT_ROOT"

  echo "Clone attempt $attempt/$MAX_CLONE_ATTEMPTS: $ATTEMPT_CHECKOUT"
  clone_status=0
  if timeout --kill-after=10 "$CLONE_TIMEOUT" \
    git clone "$REPOSITORY_URL" "$ATTEMPT_CHECKOUT"; then
    printf 'exit_code=0\n' >"$ATTEMPT_ROOT/clone_status.txt"
    REPO_ROOT="$ATTEMPT_CHECKOUT"
    echo "Clone attempt $attempt succeeded."
    break
  else
    clone_status=$?
    printf 'exit_code=%s\n' "$clone_status" >"$ATTEMPT_ROOT/clone_status.txt"
    echo "Clone attempt $attempt failed with exit code $clone_status; directory preserved."
  fi
  attempt=$((attempt + 1))
done

[ -n "$REPO_ROOT" ] || {
  echo "ERROR: all $MAX_CLONE_ATTEMPTS clone attempts failed" >&2
  exit 1
}

cd "$REPO_ROOT"
TARGET_REVISION="${REQUESTED_REVISION:-$DEFAULT_REVISION}"
RESOLVED_COMMIT="$(git rev-parse --verify "${TARGET_REVISION}^{commit}")" || {
  echo "ERROR: cannot resolve commit revision: $TARGET_REVISION" >&2
  exit 1
}
case "$RESOLVED_COMMIT" in
  ""|*[!0-9a-fA-F]*)
    echo "ERROR: git returned an invalid resolved commit hash" >&2
    exit 1
    ;;
esac
[ "${#RESOLVED_COMMIT}" -eq 40 ] || {
  echo "ERROR: git did not return a complete 40-character commit hash" >&2
  exit 1
}

git checkout --detach "$RESOLVED_COMMIT"
HEAD_COMMIT="$(git rev-parse HEAD)"
[ "$HEAD_COMMIT" = "$RESOLVED_COMMIT" ] || {
  echo "ERROR: checked-out HEAD does not equal the resolved commit" >&2
  echo "Resolved commit: $RESOLVED_COMMIT" >&2
  echo "Checked-out HEAD: $HEAD_COMMIT" >&2
  exit 1
}
echo "Verified commit: $HEAD_COMMIT"

echo "Running cloned scripts/test_vm_verdi_gui.sh"
PROJECT_ROOT="$REPO_ROOT" \
OUTPUT_BASE="$ARTIFACT_ROOT" \
bash "$REPO_ROOT/scripts/test_vm_verdi_gui.sh"
