"""Same-window resource trend, measured without holding old reports (Linux /proc)."""
import gc
import json
import sys
import time
from pathlib import Path
from statistics import median
from unittest.mock import patch
from stress_grid import BOOK, require
from rscheck.config import load_config
from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import INVENTORY_SOURCE


def resident_kib():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise RuntimeError("requires Linux VmRSS")


def run(d, count):
    root = tk.Tk()
    rss, durations = [], []
    try:
        app = RsCheckApp(root)
        cfg = d/"config.json"
        app._apply_config(load_config(cfg), cfg, "soak")
        app.excel_var.set(str(BOOK))
        app.source_mode_var.set(INVENTORY_SOURCE)
        app.inventory_var.set(str(d/"grid-inventory.json"))
        app.json_report_var.set(str(d/"soak-latest.json"))
        app.csv_report_var.set("")
        app.keep_inventory_var.set("")
        app._update_source_mode()
        root.update()
        require(root.winfo_ismapped(), "Tk not mapped")
        with patch("rscheck.gui.messagebox.showerror") as errors:
            for i in range(count):
                t = time.monotonic()
                app.run_button.invoke()
                while app.running and time.monotonic()-t < 120:
                    root.update()
                    time.sleep(.01)
                require(not app.running and not errors.called and app.summary_state_var.get() == "PASS"
                        and len(app._result_records) == 385, "bad result in soak round %d" % (i+1))
                app.result_tree.selection_set("row-%d" % (1+4*(i*17%96)))
                app._on_result_selected()
                view = app.connection_view
                app.inspector_tabs.select(view)
                root.update()
                require(view.group_no == (1+i*17%96,96) and view.canvas.find_withtag("signal"),
                        "wrong graph in soak round %d" % (i+1))
                durations.append(round(time.monotonic()-t, 3))
                gc.collect()  # comparable post-run snapshots; no allocator trim, no GUI restart
                rss.append(resident_kib())
        return {"rounds":count,"mode":"real inventory replay, one mapped GUI","post_gc_gui_rss_kib":rss,
                "round_seconds":durations,"median_round_seconds":round(median(durations),3),
                "sample_method":"Linux /proc/self/status VmRSS after each successful check and graph selection"}
    finally:
        for timer in root.tk.call("after","info"):
            root.after_cancel(timer)
        root.destroy()


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]),int(sys.argv[2]))))
