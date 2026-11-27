#!/usr/bin/env bash
# Rebuild private fixtures, exercise both positive and deliberately negative cases.
# Run from a licensed Linux graphical terminal, not from the user's DUT directory.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$root"
: "${VERDI_HOME:?Set VERDI_HOME as in your normal Verdi terminal}"
py=$(command -v "${PYTHON_BIN:-python3}")
py=$(readlink -f "$py")
export PYTHON_BIN="$py" PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1
"$py" -c 'import sys, tkinter as t; assert sys.version_info >= (3,8); r=t.Tk(); r.update(); assert r.winfo_ismapped(); r.destroy()'
base=${OUTPUT_BASE:-"$root/output"}
mkdir -p "$base"
base=$(cd "$base" && pwd -P)
run=$(mktemp -d "$base/rscheck-system.XXXXXXXX")
echo "ARTIFACT_DIR=$run"
trap 'echo "Gate failed at line $LINENO; logs retained in $run" >&2' ERR
grid="$run/grid"
mkdir -p "$grid/elab"
measure() { # common GNU time format: wall s / max PID RSS KiB / user s / sys s / exit
  local file=$1 deadline=$2
  shift 2
  echo "[$(date +%T)] ${file##*/}"
  /usr/bin/time -f '%e %M %U %S %x' -o "$file.time" timeout --kill-after=10s "$deadline" "$@" >"$file.log" 2>&1
}
export GUI_ONLINE_ITERATIONS=30 GUI_POSITION_HIERARCHY_ITERATIONS=25 GUI_CLK_WITHOUT_RST_ITERATIONS=25
export GUI_RS_CFG_DONTCARE_ITERATIONS=25 GUI_RS_CFG_NA_ITERATIONS=25 GUI_CRG_SOURCE_MAPPING_ITERATIONS=25
export GUI_CRG_TRACE_DEPTH_ITERATIONS=25 GUI_STRESS_ITERATIONS=120 GUI_LOAD_ROWS=20000 GUI_VISIBLE_SECONDS=0
export KEEP_VERDI_GUI=0
measure "$run/gui" 1800 env OUTPUT_BASE="$run/gui" bash scripts/test_vm_verdi_gui.sh
gate=$(find "$run/gui" -maxdepth 1 -name 'verdi_gui_test.*' -type d -print -quit)
coll="$gate/npi_build/rs_npi_collector"
export RSCHECK_ROUTE_COLLECTOR="$coll" RSCHECK_ROUTE_KDB="$gate/example_elab/kdb.elab++"
lib=${NPI_LIB_DIR:-"$VERDI_HOME/share/NPI/lib/${NPI_PLATFORM:-LINUX64}"}
[[ -f "$lib/libNPI.so" ]] || lib="$VERDI_HOME/share/NPI/lib/linux64"
[[ -x "$coll" && -d "$RSCHECK_ROUTE_KDB" && -f "$lib/libNPI.so" ]]
export LD_LIBRARY_PATH="$lib:${LD_LIBRARY_PATH:-}" RSCHECK_ROUTE_NPI_LIB="$lib"
build() {
  local dest=$1 sv=$2 top=$3 db=$4
  mkdir -p "$dest"
  (cd "$dest" &&
   timeout 180 "${VERICOM_BIN:-$VERDI_HOME/bin/vericom}" -sv "$sv" &&
   timeout 180 "${ELABCOM_BIN:-$VERDI_HOME/bin/elabcom}" -top "$top" -elab "$dest/$db.elab++") >"$dest/build.log" 2>&1
  [[ -d "$dest/$db.elab++" ]]
}
for pair in driver:rs_driver_map:driver_top link:rs_interconnect:link_top boundary:rs_position_boundary:boundary_top; do
  IFS=: read -r key rtl top <<<"$pair"
  build "$run/$key" "$root/examples/rtl/$rtl.sv" "$top" test
  export "RSCHECK_${key^^}_KDB=$run/$key/test.elab++" "RSCHECK_${key^^}_COLLECTOR=$coll"
done
measure "$run/full" 600 "$py" -B -m unittest discover -v
grep -qx 'OK' "$run/full.log" # no skipped integration gates
for i in $(seq 1 10); do
  measure "$run/hot-$i" 240 "$py" -B -m unittest tests.test_driver_port_map.LiveDriverTests \
    tests.test_position_boundaries.LiveBoundaryTests tests.test_interconnect.InterconnectTests
  grep -qx 'OK' "$run/hot-$i.log"
done
build "$grid/elab" "$root/tests/fixtures/scale_grid.sv" stress_top grid
db="$grid/elab/grid.elab++"
testpy=("$py" -B scripts/stress_grid.py)
"${testpy[@]}" prepare "$grid" >"$grid/fixture.json"
measure "$grid/excel20k" 420 "${testpy[@]}" excel20k "$grid"
common=("$py" -B -m rscheck check --excel tests/fixtures/scale_grid.xlsx --config "$grid/config.json")
live=(--collector "$coll" --elab-db "$db" --npi-timeout 300)
for i in 1 2 3; do
  measure "$grid/live-$i" 420 "${common[@]}" "${live[@]}" --keep-inventory "$grid/grid-inventory.json" \
    --json-report "$grid/live.json" --csv-report "$grid/report.csv" --match-report "$grid/match.txt"
  "${testpy[@]}" audit "$grid" --report "$grid/live.json" >"$grid/oracle-$i.json"
  cmp "$grid/oracle-1.json" "$grid/oracle-$i.json"
done
measure "$grid/replay" 240 "${common[@]}" --inventory "$grid/grid-inventory.json" --json-report "$grid/replay.json" \
  --csv-report "$grid/replay.csv" --match-report "$grid/replay.txt"
for test in fault fuzzy; do measure "$grid/$test" 180 "${testpy[@]}" "$test" "$grid"; done
for pair in grid-gui:gui cancel:cancel; do
  measure "$grid/${pair%:*}" 540 "${testpy[@]}" "${pair#*:}" "$grid" --collector "$coll" --kdb "$db"
done
export RSCHECK_HIERARCHY_CACHE_DIR="$grid/picker-cache"
picker=("$py" -B scripts/test_position_hierarchy_gui_smoke.py --collector "$coll" --elab-db "$db"
        --timeout 120 --visible-seconds 0 --iterations 12)
measure "$grid/picker-position" 360 "${picker[@]}" --target position --expect-first collector --exercise-clear \
  --regex '^stress_top\.banks\[40\]\.tile$' --expected-path 'stress_top.banks[40].tile'
measure "$grid/picker-crg" 360 "${picker[@]}" --target CRG_source --expect-first disk \
  --regex '^stress_top\.src$' --expected-path stress_top.src
for n in 1000 5000 10000 20000; do
  name=$n; if [[ $n == 20000 ]]; then name=20k; fi
  measure "$grid/offline-$name" 360 "$py" -B scripts/test_rscheck_gui_smoke.py --generated-rows "$n" --iterations 1 \
    --timeout 240 --visible-tab results --visible-seconds 0
done
# Separate report paths; all four independent processes read the SAME KDB.
pids=()
for i in 1 2 3 4; do
  measure "$grid/parallel-$i" 420 "${common[@]}" "${live[@]}" --json-report "$grid/parallel-$i.json" &
  pids+=("$!")
done
failed=0
for pid in "${pids[@]}"; do wait "$pid" || failed=1; done
[[ $failed == 0 ]]
measure "$grid/soak" 300 "$py" -B scripts/stress_soak.py "$grid" 30
# This is a diagnostic, not a functional PASS condition. Preserve Tcl stderr.
measure "$grid/tk-reopen" 60 "$py" -B scripts/probe_tk_reopen.py
"$py" -B scripts/stress_results.py "$run"
