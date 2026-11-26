# Select a source-checkout runtime without changing the system Python.
rscheck_select_python() {
  local candidate
  for candidate in "$@"; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      return 0
    fi
  done
  echo "ERROR: Python 3.8 or newer is required; set PYTHON_BIN to a compatible interpreter (checked: $*)." >&2
  return 1
}

rscheck_resolve_python() {
  if [ -n "${PYTHON_BIN:-}" ]; then
    rscheck_select_python "$PYTHON_BIN"
  else
    rscheck_select_python python3 python3.8
  fi
}
