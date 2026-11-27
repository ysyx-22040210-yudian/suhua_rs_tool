#!/usr/bin/env bash
# Run from a licensed Verdi + Tk terminal. Do not modify the user's DUT or KDB.
set -euo pipefail
: "${VERDI_HOME:?Set VERDI_HOME}"
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON_BIN:-python3}"
out="$(mktemp -d "${TMPDIR:-/tmp}/rscheck-bounds.XXXXXXXX")"
lib="$VERDI_HOME/share/NPI/lib/LINUX64"
[[ -f "$lib/libNPI.so" ]] || lib="$VERDI_HOME/share/NPI/lib/linux64"
export LD_LIBRARY_PATH="$lib:$VERDI_HOME/share/NPI/lib/linux64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
make -C "$root/npi" VERDI_HOME="$VERDI_HOME" BUILD_DIR="$out/build"
mkdir "$out/elab"
cd "$out/elab"
"$VERDI_HOME/bin/vericom" -sv "$root/examples/rtl/rs_position_boundary.sv"
"$VERDI_HOME/bin/elabcom" -top boundary_top -elab "$out/elab/boundary.elab++"
export RSCHECK_BOUNDARY_COLLECTOR="$out/build/rs_npi_collector" RSCHECK_BOUNDARY_KDB="$out/elab/boundary.elab++"
cd "$root"
"$py" -B -m unittest -v tests.test_position_boundaries
printf 'RS_POSITION_BOUNDARY_GUI_PASS  artifacts=%s\n' "$out"
