#!/usr/bin/env bash

# Load a trusted site environment in a supervised disposable process, import
# only its exported environment, then exec the fixed command. Sourced shell
# state never controls this parent process or its command arguments.
{ set +x; } 2>/dev/null
set -Ee -o pipefail
umask 077

if [ "$#" -lt 2 ]; then
  echo "ERROR: run_with_env_file.sh requires ENV_FILE and COMMAND" >&2
  exit 2
fi

RSCHECK_SITE_ENV_FILE="$1"
shift
readonly RSCHECK_SITE_ENV_FILE
readonly -a RSCHECK_SITE_ENV_COMMAND=("$@")
RSCHECK_SITE_ENV_CWD="$(pwd -P)"
readonly RSCHECK_SITE_ENV_CWD
declare -a RSCHECK_PARENT_EXPORT_NAMES=()
while IFS= read -r exported_name; do
  [ -n "$exported_name" ] && RSCHECK_PARENT_EXPORT_NAMES+=("$exported_name")
done < <(compgen -e)

case "$RSCHECK_SITE_ENV_FILE" in
  /*) ;;
  *)
    echo "ERROR: VERDI_ENV_FILE must be an absolute path" >&2
    exit 1
    ;;
esac
[ -f "$RSCHECK_SITE_ENV_FILE" ] && [ -r "$RSCHECK_SITE_ENV_FILE" ] || {
  echo "ERROR: VERDI_ENV_FILE is not a readable regular file" >&2
  exit 1
}
command -v mktemp >/dev/null 2>&1 || {
  echo "ERROR: mktemp is required to load VERDI_ENV_FILE" >&2
  exit 1
}

RSCHECK_SITE_ENV_TEMP="$(mktemp -d /tmp/rscheck_site_env.XXXXXXXX)"
RSCHECK_SITE_ENV_DUMP="$RSCHECK_SITE_ENV_TEMP/environment.bin"
RSCHECK_SITE_ENV_MARKER="$RSCHECK_SITE_ENV_TEMP/complete"
readonly RSCHECK_SITE_ENV_TEMP RSCHECK_SITE_ENV_DUMP RSCHECK_SITE_ENV_MARKER

cleanup_site_environment() {
  /bin/rm -f -- "$RSCHECK_SITE_ENV_DUMP" "$RSCHECK_SITE_ENV_MARKER"
  /bin/rmdir -- "$RSCHECK_SITE_ENV_TEMP" 2>/dev/null || true
}
trap cleanup_site_environment EXIT

source_process_status=0
(
  preserve_lm=0
  preserve_snps=0
  original_lm="${LM_LICENSE_FILE-}"
  original_snps="${SNPSLMD_LICENSE_FILE-}"
  [ -n "$original_lm" ] && preserve_lm=1
  [ -n "$original_snps" ] && preserve_snps=1
  readonly preserve_lm preserve_snps original_lm original_snps

  RSCHECK_SITE_ENV_SOURCE_STATUS=0
  set -a
  {
    # shellcheck disable=SC1090
    source "$RSCHECK_SITE_ENV_FILE" || RSCHECK_SITE_ENV_SOURCE_STATUS=$?
    builtin set +x +v
    builtin trap - DEBUG RETURN ERR EXIT
  } >/dev/null 2>&1
  set +a
  { set +x; } 2>/dev/null
  set -Ee -o pipefail
  umask 077

  [ "$RSCHECK_SITE_ENV_SOURCE_STATUS" -eq 0 ] ||
    exit "$RSCHECK_SITE_ENV_SOURCE_STATUS"
  if [ "$preserve_lm" -eq 1 ]; then
    export LM_LICENSE_FILE="$original_lm"
  fi
  if [ "$preserve_snps" -eq 1 ]; then
    export SNPSLMD_LICENSE_FILE="$original_snps"
  fi

  unset VERDI_ENV_FILE BASH_ENV ENV PS4 BASH_XTRACEFD 2>/dev/null || true
  /usr/bin/env -0 >"$RSCHECK_SITE_ENV_DUMP" || exit 70
  printf 'complete\n' >"$RSCHECK_SITE_ENV_MARKER" || exit 71
) || source_process_status=$?

if [ "$source_process_status" -ne 0 ]; then
  echo "ERROR: VERDI_ENV_FILE initialization failed with exit code $source_process_status" >&2
  exit 1
fi
completion_marker=""
if [ -f "$RSCHECK_SITE_ENV_MARKER" ]; then
  IFS= read -r completion_marker <"$RSCHECK_SITE_ENV_MARKER" || true
fi
if [ "$completion_marker" != complete ]; then
  echo "ERROR: VERDI_ENV_FILE exited before environment initialization completed" >&2
  exit 1
fi
[ -f "$RSCHECK_SITE_ENV_DUMP" ] || {
  echo "ERROR: VERDI_ENV_FILE did not produce an environment" >&2
  exit 1
}

declare -A RSCHECK_IMPORTED_ENV_NAMES=()
while IFS= read -r -d '' environment_entry; do
  environment_name="${environment_entry%%=*}"
  case "$environment_name" in
    BASH_ENV|ENV|SHELLOPTS|BASHOPTS|PS4|BASH_XTRACEFD|VERDI_ENV_FILE|RSCHECK_*|BASH_FUNC_*)
      continue
      ;;
  esac
  RSCHECK_IMPORTED_ENV_NAMES["$environment_name"]=1
  builtin export -- "$environment_entry"
done <"$RSCHECK_SITE_ENV_DUMP"

for exported_name in "${RSCHECK_PARENT_EXPORT_NAMES[@]}"; do
  case "$exported_name" in
    BASH_ENV|ENV|SHELLOPTS|BASHOPTS|PS4|BASH_XTRACEFD|VERDI_ENV_FILE|RSCHECK_*|BASH_FUNC_*)
      unset "$exported_name" 2>/dev/null || true
      continue
      ;;
  esac
  if [ -z "${RSCHECK_IMPORTED_ENV_NAMES[$exported_name]+present}" ]; then
    unset "$exported_name"
  fi
done

cleanup_site_environment
trap - EXIT
unset VERDI_ENV_FILE BASH_ENV ENV PS4 BASH_XTRACEFD 2>/dev/null || true
builtin cd -- "$RSCHECK_SITE_ENV_CWD"
echo "Site environment loaded from explicit VERDI_ENV_FILE (output suppressed)."
builtin exec /usr/bin/env \
  -u BASH_ENV \
  -u ENV \
  -u SHELLOPTS \
  -u BASHOPTS \
  -u PS4 \
  -u BASH_XTRACEFD \
  "${RSCHECK_SITE_ENV_COMMAND[@]}"
