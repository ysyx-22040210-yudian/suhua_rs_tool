"""Make a small, path/license-free evidence file; refuse to summarize incomplete gates."""
import csv
import hashlib
import json
import os
import platform
import re
import statistics
import sys
from pathlib import Path

from stress_grid import ROOT, BOOK, require


def read(path):
    return path.read_text(encoding="utf-8", errors="replace")


def timing(path):
    sec, rss, user, system, rc = read(path).split()
    require(rc == "0", "nonzero exit: "+path.name)
    return dict(seconds=float(sec), max_rss_kib=int(rss), user_s=float(user), system_s=float(system))


def suite(path):
    log = read(path)
    m = re.search(r"^Ran (\d+) tests? in ([0-9.]+)s\s+OK\s*\Z", log, re.M)
    require(m and not re.search(r"\.\.\. skipped", log), "missing/failed/skipped tests in "+path.name)
    return {"tests":int(m[1]), "seconds":float(m[2]), "skips":0}


def json_line(path):
    return json.loads(read(path).strip().splitlines()[-1])


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def residual(run):
    # Only native tool/GUI processes whose argv explicitly identifies THIS run.
    hits = []
    root = os.fsencode(str(run)) + b"/"
    for p in Path("/proc").glob("[0-9]*"):
        try:
            args = (p/"cmdline").read_bytes().split(b"\0")
            exe = Path(os.readlink(str(p/"exe"))).name.lower()
        except OSError:
            continue
        if int(p.name) == os.getpid() or not any(a.startswith(root) for a in args):
            continue
        tool = "collector" in exe or any(n in exe for n in ("verdi","novas","ntrace","elabcom","vericom"))
        gui = "python" in exe and any(a.endswith((b"stress_grid.py", b"test_rscheck_gui_smoke.py",
                                                    b"test_position_hierarchy_gui_smoke.py")) or a == b"rscheck" for a in args)
        if tool or gui:
            hits.append(exe)
    require(not hits, "run-owned native/GUI processes still live: "+str(hits))
    return hits


def collect(run):
    grid = run/"grid"
    gate = read(run/"gui.log")
    require("PASS: partial/clean hierarchy discovery" in gate, "main GUI gate incomplete")
    pattern = (r"^GUI_SMOKE_PASS: state=(\w+) rows=行数 (\d+) errors=错误 (\d+) warnings=警告 (\d+) "
               r"mode=(\w+) case=([\w-]+) iterations=(\d+) window=mapped")
    gui = [dict(state=m[0], rows=int(m[1]), errors=int(m[2]), warnings=int(m[3]), mode=m[4], case=m[5],
                iterations=int(m[6])) for m in re.findall(pattern, gate, re.M)]
    require(len(gui) == 12 and any(g["rows"] == 20000 for g in gui), "missing expected GUI checks")
    full = suite(run/"full.log")
    repeats = [suite(run/("hot-%d.log" % i)) for i in range(1,11)]
    oracles = [json_line(grid/("oracle-%d.json" % i)) for i in range(1,4)]
    require(all(o == oracles[0] for o in oracles), "unstable hardware evidence")
    reports = {"json": ("live.json","replay.json"), "csv":("report.csv","replay.csv"),
               "text":("match.txt","replay.txt")}
    for _,(live,replay) in reports.items():
        require(sha(grid/live) == sha(grid/replay), "replay mismatch "+live)
    with (grid/"report.csv").open(encoding="utf-8-sig",newline="") as stream:
        csv.field_size_limit(32*1024*1024)
        records = list(csv.DictReader(stream))
        require(sum(r["status"]=="PASS" for r in records)==385 and sum(r["status"]=="SKIPPED" for r in records)==96
                and len(records)==481, "CSV missing row-level evidence")
    require(all(sha(grid/("parallel-%d.json" % i)) == sha(grid/"live.json") for i in range(1,5)), "parallel drift")
    fault = json_line(grid/"fault.log")
    require(fault["diagnostics"] == {"2":["STEP_MISMATCH"],"3":["CLK_CONNECTION_MISMATCH","RST_CONNECTION_MISMATCH"],
            "5":["STEP_MISMATCH"],"6":["RS_CFG_EN_LABEL_MISMATCH"],"7":["EXCEL_FIELD_BLANK"]}, "fault codes")
    load = {}
    for n,label in ((1000,"1000"),(5000,"5000"),(10000,"10000"),(20000,"20k")):
        checks = re.findall(pattern, read(grid/("offline-"+label+".log")), re.M)
        require(len(checks) == 1 and checks[0][:5] == ("PASS",str(n),"0","0","offline"), "offline scale "+label)
        load[n] = timing(grid/("offline-"+label+".time"))
    picker = {}
    for name in ("position","crg"):
        m = re.search(r"browse_calls=(\d+) first=(\w+) memory_hits=(\d+) max_reopen_ms=(\d+) manual_reload=(\d+)",
                      read(grid/("picker-"+name+".log")))
        require(m, "missing picker marker: "+name)
        picker[name] = dict(browse_calls=int(m[1]), first=m[2], memory_hits=int(m[3]), max_reopen_ms=int(m[4]),
                            manual_reload=int(m[5]))
    src = sorted(list((ROOT/"rscheck").rglob("*.py"))+list((ROOT/"npi").glob("*.cpp"))+[ROOT/"npi/Makefile"])
    h = hashlib.sha256()
    for path in src:
        data = path.read_bytes().replace(b"\r\n",b"\n")
        h.update(path.relative_to(ROOT).as_posix().encode()+b"\0"+hashlib.sha256(data).digest())
    def runs(prefix,count):
        return [timing(grid/(prefix+str(i)+".time")) for i in range(1,count+1)]
    extra = {tag:json_line(grid/(tag+".log")) for tag in ("grid-gui","cancel","fuzzy","excel20k","soak")}
    require(extra["grid-gui"]["sha256"] == extra["cancel"]["sha256"] == oracles[0]["sha256"], "GUI evidence drift")
    require(extra["cancel"]["cancelled_while_native_running"] == 12, "cancellation coverage")
    reopen = read(grid/"tk-reopen.log")
    require("TK_REOPEN_CYCLES=8" in reopen, "Tk reproduction did not finish")
    callbacks = r'invalid command name "[^"\n]*_poll_events"'
    tk_notes = {"reopen_cycles":8,"reopen_callback_errors":len(re.findall(callbacks,reopen)),
                "full_suite_callback_errors":len(re.findall(callbacks,read(run/"full.log")))}
    samples = extra["soak"]["post_gc_gui_rss_kib"]
    require(extra["soak"]["rounds"] == len(samples) == 30,"incomplete same-window soak")
    findings = []
    if tk_notes["reopen_callback_errors"] or tk_notes["full_suite_callback_errors"]:
        findings.append("tk_after_callback_after_window_close")
    if samples[-1] > samples[4] + 5*1024:
        findings.append("post_gc_rss_growth_over_repeated_large_reports")
    import tkinter as tk
    from rscheck import __version__
    result = {
        "schema":"rscheck.stress.v1","status":"PASS_WITH_FINDINGS" if findings else "PASS",
        "functional_gates":"PASS","observations":findings,"tk_lifecycle":tk_notes,
        "environment":{"python":platform.python_version(),"tk":tk.TkVersion,"kernel":platform.release(),
                       "logical_cpus":os.cpu_count(),"app":__version__},
        "source_core_sha256_lf":h.hexdigest(),"workbook_sha256":sha(BOOK),
        "rtl_fixture_sha256_lf":hashlib.sha256((BOOK.with_suffix(".sv")).read_bytes().replace(b"\r\n",b"\n")).hexdigest(),
        "full_suite":{**full,**{"process":timing(run/"full.time")}},
        "special_repeats":{"rounds":len(repeats),"tests":sum(s["tests"] for s in repeats),"skips":0,
                           "median_round_s":statistics.median(s["seconds"] for s in repeats)},
        "master_gui":{"process":timing(run/"gui.time"),"cases":gui},
        "xlsx":json_line(grid/"fixture.json"),"oracle":oracles[0],
        "serial_online":runs("live-",3),"concurrent_online_4":runs("parallel-",4),
        "offline_replay":timing(grid/"replay.time"),"byte_identical_replay":["JSON","CSV","match.txt"],
        "report_bytes":{kind:(grid/a).stat().st_size for kind,(a,b) in reports.items()},
        "fault_injection":fault,"offline_gui_rows":load,"picker":picker,
        "detail":extra,"other_timing":{n:timing(grid/(n+".time")) for n in
                                      ("fault","fuzzy","grid-gui","cancel","excel20k","soak","tk-reopen")},
        "run_owned_native_or_gui_processes_at_end":residual(run)}
    return result


if __name__ == "__main__":
    run = Path(sys.argv[1]).resolve()
    summary = collect(run)
    (run/"metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)+"\n",encoding="utf-8")
    print("PROJECT_STRESS_{}: metrics.json".format(summary["status"]))
