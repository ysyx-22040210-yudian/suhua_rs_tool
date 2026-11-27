#!/usr/bin/env bash
# Run in the licensed Verdi + Tk terminal. Builds only in a fresh temp directory.
set -euo pipefail
: "${VERDI_HOME:?Set VERDI_HOME}"
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON_BIN:-python3}"
out="$(mktemp -d "${TMPDIR:-/tmp}/rscheck-driver.XXXXXXXX")"
lib="$VERDI_HOME/share/NPI/lib/LINUX64"
[[ -f "$lib/libNPI.so" ]] || lib="$VERDI_HOME/share/NPI/lib/linux64"
export LD_LIBRARY_PATH="$lib:$VERDI_HOME/share/NPI/lib/linux64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
make -C "$root/npi" VERDI_HOME="$VERDI_HOME" BUILD_DIR="$out/build"
mkdir "$out/elab"
cd "$out/elab"
"$VERDI_HOME/bin/vericom" -sv "$root/examples/rtl/rs_driver_map.sv"
"$VERDI_HOME/bin/elabcom" -top driver_top -elab "$out/elab/driver.elab++"
export RSCHECK_DRIVER_COLLECTOR="$out/build/rs_npi_collector" RSCHECK_DRIVER_KDB="$out/elab/driver.elab++"
cd "$root"
# A missing display must not silently skip the GUI check in this VM gate.
"$py" -c 'import tkinter as t; r=t.Tk(); r.update(); r.destroy()'
"$py" -B -m unittest -v tests.test_driver_port_map
printf 'RS_DRIVER_PORT_MAP_GUI_PASS  artifacts=%s\n' "$out"
