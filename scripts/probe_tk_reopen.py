"""Lifecycle diagnostic: run in a fresh process, e.g. python scripts/probe_tk_reopen.py 2>reopen.log."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rscheck.gui import RsCheckApp, tk


def main():
    for _ in range(8):
        root = tk.Tk()
        app = RsCheckApp(root)
        root.update()
        root.after(15,app._on_close)  # exercise the actual idle-window close handler
        root.mainloop()
    # Let Tcl process any timer left behind by the last closed window.
    final = tk.Tk()
    final.after(300, final.destroy)
    final.mainloop()
    print("TK_REOPEN_CYCLES=8; inspect stderr for 'invalid command name ..._poll_events'")


if __name__ == "__main__":
    main()
