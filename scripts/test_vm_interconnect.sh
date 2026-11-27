#!/usr/bin/env bash
# Run inside a configured Verdi + Tk environment. Never modify an existing KDB.
set -euo pipefail
: "${VERDI_HOME:?Set VERDI_HOME to your licensed Verdi installation}"
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON_BIN:-python3}"
out="$(mktemp -d "${TMPDIR:-/tmp}/rscheck-links.XXXXXXXX")"
lib="$VERDI_HOME/share/NPI/lib/LINUX64"
if [[ ! -f "$lib/libNPI.so" ]]; then lib="$VERDI_HOME/share/NPI/lib/linux64"; fi
export LD_LIBRARY_PATH="$lib:$VERDI_HOME/share/NPI/lib/linux64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
make -C "$root/npi" VERDI_HOME="$VERDI_HOME" BUILD_DIR="$out/build"
mkdir "$out/elab"
cd "$out/elab"
"$VERDI_HOME/bin/vericom" -sv "$root/examples/rtl/rs_interconnect.sv"
"$VERDI_HOME/bin/elabcom" -top link_top -elab "$out/elab/links.elab++"
cd "$root"
export RSCHECK_LINK_COLLECTOR="$out/build/rs_npi_collector" RSCHECK_LINK_KDB="$out/elab/links.elab++"
export RSCHECK_ROUTE_NPI_LIB="$lib"
"$py" -B -m unittest -v tests.test_interconnect
printf 'RS_INTERCONNECT_GUI_PASS  artifacts=%s\n' "$out"
