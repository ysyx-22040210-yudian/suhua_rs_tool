"""Native Tk visual system. No external fonts, images, or theme packages."""
from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk

from .gui_rounded import RoundedElements, skin


# Warm paper and graphite are the scaffolding; clay is an action, not a verdict.
PAGE, PAPER, LINE = "#faf9f5", "#fffefa", "#dcd8ce"
INK, MUTED = "#22211f", "#625f58"
NAV, NAV_HOVER, NAV_SELECTED = "#f0eee6", "#e9e5db", "#e7d9cb"
CLAY, ACTION, CLAY_INK, WASH = "#d97757", "#984b34", "#813f2c", "#f5e6db"
SUCCESS, RED, AMBER, BLUE = "#446246", "#9a3440", "#815618", "#415f78"
FAIL_WASH, WARN_WASH = "#fbefec", "#faf0d9"
FIELD_LINE, FOCUS, MONITOR = "#b9b3a7", "#995037", "#282624"

# Draw the same small instrument symbols on every platform, without icon fonts.
_GLYPHS = {
    "setup": [(2,4,15,4),(5,2,5,6),(2,9,15,9),(12,7,12,11),(2,14,15,14),(7,12,7,16)],
    "results": [(2,2,2,15),(2,15,16,15),(6,8,6,12),(10,4,10,12),(14,6,14,12)],
    "log": [(2,3,16,3),(2,3,2,15),(2,15,16,15),(16,3,16,15),(5,7,7,9),(7,9,5,11),(9,11,13,11)],
    "rules": [(4,4,14,4),(4,4,4,14),(14,4,14,14),(4,14,14,14),(7,7,11,7),(7,7,7,11),(7,11,11,11),(11,7,11,11),(7,1,7,3),(11,1,11,3),(7,15,7,17),(11,15,11,17),(1,7,3,7),(1,11,3,11),(15,7,17,7),(15,11,17,11)],
    "positions": [(3,2,7,2),(3,2,3,6),(3,6,7,6),(7,2,7,6),(5,7,5,14),(5,9,11,9),(5,14,11,14),(12,7,15,7),(15,7,15,10),(12,10,15,10),(12,7,12,10),(12,12,15,12),(15,12,15,16),(12,16,15,16),(12,12,12,16)],
    "crg": [(1,11,5,11),(5,11,5,5),(5,5,9,5),(9,5,9,13),(9,13,13,13),(13,13,13,6),(13,6,17,6)],
}


class StudioTheme:
    def __init__(self, root):
        self.root = root
        self.style = s = ttk.Style(root)
        if "clam" in s.theme_names():
            s.theme_use("clam")
        self.rounded = RoundedElements(root, s)
        available = {name.casefold(): name for name in font.families(root)}
        def choose(names, default):
            return next((available[n.casefold()] for n in names if n.casefold() in available),
                        font.nametofont(default, root=root).actual("family"))
        self.face = choose(("Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC",
                            "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Microsoft YaHei"), "TkDefaultFont")
        self.mono = choose(("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"), "TkFixedFont")
        self.serif = choose(("Source Han Serif SC", "Noto Serif CJK SC", "Songti SC",
                             "SimSun", "STSong", "Georgia"), "TkHeadingFont")
        self.wordmark = choose(("Georgia", "Noto Serif", "DejaVu Serif", "Times New Roman"), "TkHeadingFont")
        for name in ("TkDefaultFont", "TkTextFont"):
            font.nametofont(name, root=root).configure(family=self.face, size=10)
        self.f = lambda size, weight="normal": (self.face, size, weight)
        self.code = lambda size: (self.mono, size)
        self.icons = {}
        mark = self.mark = tk.PhotoImage(master=root, width=32, height=32)
        # Our own RTL pulse mark; no site assets or font downloads.
        for y in range(32):
            for x in range(32):
                dx, dy = max(7.5 - x, x - 23.5, 0), max(7.5 - y, y - 23.5, 0)
                if dx * dx + dy * dy <= 64:
                    mark.put(ACTION, (x, y))
        for rect in ((4,20,11,22), (9,10,11,22), (9,10,17,12), (15,10,17,22),
                     (15,20,23,22), (21,10,23,22), (21,10,28,12)):
            mark.put(PAPER, to=rect)
        try:
            root.iconphoto(False, mark)
        except tk.TclError:
            pass  # Some window managers provide their own application icon.
        s.configure(".", font=self.f(10), foreground=INK, background=PAPER,
                    selectbackground="#eddbcd", selectforeground=INK)
        s.configure("TFrame", background=PAPER)
        s.configure("Page.TFrame", background=PAGE)
        skin(s, "Surface.TFrame", self.rounded.element("card", 16, PAGE, PAPER, LINE))
        skin(s, "Metric.TFrame", self.rounded.element("metric", 13, PAGE, PAPER, LINE))
        skin(s, "Console.TFrame", self.rounded.element("console", 13, PAPER, MONITOR, MONITOR))
        s.configure("TLabel", background=PAPER, foreground=INK)
        s.configure("Muted.TLabel", background=PAPER, foreground=MUTED, font=self.f(9))
        s.configure("Title.TLabel", foreground=INK, font=(self.serif, 19, "bold"))
        s.configure("Page.Title.TLabel", background=PAGE)
        s.configure("Page.Muted.TLabel", background=PAGE)
        s.configure("CardTitle.TLabel", font=self.f(11, "bold"))
        label_content = [("Label.padding", {"sticky": "nsew", "children": [
            ("Label.label", {"sticky": "nsew"})]})]
        for style, radius, outside, bg, fg in (
            ("Index.TLabel", 7, PAPER, WASH, CLAY_INK),
            ("Badge.TLabel", 10, PAPER, "#eeeae0", MUTED),
            ("Dirty.Badge.TLabel", 10, PAPER, WARN_WASH, AMBER),
            ("Live.Badge.TLabel", 11, PAGE, WASH, CLAY_INK),
            ("Offline.Badge.TLabel", 11, PAGE, "#e7ecea", "#415c52"),
        ):
            skin(s, style, self.rounded.element(style, radius, outside, bg, bg), label_content)
            s.configure(style, background=outside, foreground=fg, font=self.f(9, "bold"), padding=(9, 3))
        s.configure("Index.TLabel", font=(self.mono, 9, "bold"), padding=(7, 2))
        s.configure("TLabelframe", background=PAPER, bordercolor=LINE, relief="solid", borderwidth=1)
        s.configure("TLabelframe.Label", background=PAPER, font=self.f(10, "bold"), foreground=INK)
        self._fields()
        for toggle in ("TCheckbutton", "TRadiobutton"):
            s.configure(toggle, background=PAPER, foreground=INK, focuscolor=FOCUS, indicatorcolor=PAPER)
            s.map(toggle, background=[("active", PAPER)], indicatorcolor=[("selected", "#d7b8a4")])
        self._buttons()
        s.configure("Treeview", background=PAPER, fieldbackground=PAPER, foreground=INK,
                    borderwidth=0, rowheight=max(28, font.nametofont("TkDefaultFont", root=root).metrics("linespace") + 9))
        s.configure("Treeview.Heading", background="#f1eee7", foreground="#514b43", font=self.f(9,"bold"),
                    relief="flat", padding=(7, 5), bordercolor=LINE, lightcolor="#f1eee7", darkcolor=LINE)
        s.map("Treeview.Heading", background=[("active", NAV_SELECTED)])
        s.map("Treeview", background=[("selected", "#f0dfcf")], foreground=[("selected", "#3f3028")])
        s.configure("TScrollbar", background="#ccc7bc", troughcolor="#f4f2ec", borderwidth=0,
                    bordercolor=PAPER, lightcolor="#ccc7bc", darkcolor="#ccc7bc", arrowsize=11)
        s.configure("TPanedwindow", background=PAGE)
        s.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=0)
        tab = self.rounded.element("tab", 9, PAPER, PAPER, LINE, (
            (("selected", "focus"), WASH, FOCUS, 2), ("selected", WASH, "#d7b8a4", 1),
            ("focus", PAPER, FOCUS, 2), ("active", NAV_HOVER, LINE, 1)))
        skin(s, "TNotebook.Tab", tab, [("Notebook.padding", {"sticky":"nsew", "children":[
            ("Notebook.label", {"sticky":"nsew"})]})])
        s.configure("TNotebook.Tab", foreground=MUTED, padding=(10,4), expand=0)
        s.map("TNotebook.Tab", foreground=[("selected", CLAY_INK)])
        s.layout("Workspace.TNotebook.Tab", [])
        s.configure("Workspace.TNotebook", background=PAGE, borderwidth=0, tabmargins=0,
                    lightcolor=PAGE, darkcolor=PAGE, bordercolor=PAGE)
        s.configure("Horizontal.TProgressbar", background=ACTION, troughcolor=WASH,
                    bordercolor=PAPER, lightcolor=ACTION, darkcolor=ACTION, thickness=5)

    def _fields(self):
        s = self.style
        field = self.rounded.element("field", 9, PAPER, "#fffefa", FIELD_LINE, (
            ("disabled", "#f2f0ea", LINE, 1), ("focus", PAPER, FOCUS, 2),
            ("readonly", "#f5f3ed", FIELD_LINE, 1)))
        for style in ("TEntry", "TSpinbox"):
            s.configure(style, fieldbackground=PAPER, foreground=INK, background=PAPER,
                        padding=(7, 4), arrowsize=10, arrowcolor=MUTED,
                        selectbackground="#e9cbb6", selectforeground=INK)
            s.map(style, fieldbackground=[("disabled", "#f2f0ea"), ("readonly", "#f5f3ed")],
                  foreground=[("disabled", MUTED)])
        skin(s, "TEntry", field, [("Entry.padding", {"sticky":"nsew", "children":[
            ("Entry.textarea", {"sticky":"nsew"})]})])
        # Keep the stock arrow names and textarea: click repeat, selection, paste,
        # keyboard stepping and validation still run in ttk, not custom handlers.
        skin(s, "TSpinbox", field, [("Spinbox.padding", {"sticky":"nsew", "children":[
            ("null", {"side":"right", "children":[
                ("Spinbox.uparrow", {"side":"top", "sticky":"e"}),
                ("Spinbox.downarrow", {"side":"bottom", "sticky":"e"})]}),
            ("Spinbox.textarea", {"sticky":"nsew"})]})])

    def _buttons(self):
        s = self.style
        label = [("Button.padding", {"sticky":"nsew", "children":[
            ("Button.label", {"sticky":"nsew"})]})]
        variants = (
            # style, backdrop, fill, ink, border, hover, press, inner padding
            ("TButton", PAPER, PAPER, INK, FIELD_LINE, "#f1ede4", "#e8e2d8", (11, 6)),
            ("Primary.TButton", PAPER, ACTION, PAPER, ACTION, "#86402b", "#713421", (15, 7)),
            ("Danger.TButton", PAPER, PAPER, RED, "#d4b9b6", FAIL_WASH, "#f1dcd7", (11, 6)),
            ("Quiet.TButton", PAPER, PAPER, CLAY_INK, PAPER, "#f5ede4", WASH, (9, 5)),
            ("Nav.TButton", NAV, NAV, INK, NAV, NAV_HOVER, NAV_SELECTED, (11, 9)),
            ("Selected.Nav.TButton", NAV, NAV_SELECTED, "#4c382e", NAV_SELECTED,
             "#e4d3c3", "#ddc6b4", (11, 9)),
        )
        for name, outside, bg, fg, edge, hover, press, pad in variants:
            backgrounds = [(name, outside)]
            if name in ("TButton", "Primary.TButton", "Danger.TButton"):
                backgrounds.append(("Page." + name, PAGE))  # database footer sits on paper, not the card
            for variant, backdrop in backgrounds:
                focused = INK if name == "Primary.TButton" else FOCUS
                shape = self.rounded.element(variant, 12, backdrop, bg, edge, (
                    ("disabled", "#efede6", "#e0dcd2", 1), ("pressed", press, focused, 1),
                    ("focus", bg, focused, 2), ("active", hover, edge, 1)))
                skin(s, variant, shape, label)
                s.configure(variant, background=backdrop, foreground=fg, padding=pad, width=-3,
                            anchor="w" if "Nav." in name else "center",
                            font=self.f(10, "bold" if "Primary" in name or "Selected" in name else "normal"))
                s.map(variant, foreground=[("disabled", "#78746a"), ("active", fg)])
        segment = self.rounded.element("segment", 12, PAPER, PAPER, LINE, (
            ("disabled", "#efede6", LINE, 1), (("selected", "focus"), WASH, FOCUS, 2),
            ("pressed", "#e4d4c4", FOCUS, 1), ("selected", WASH, "#c89579", 1),
            ("focus", PAPER, FOCUS, 2), ("active", "#f5f2e9", FIELD_LINE, 1)))
        skin(s, "Segment.TRadiobutton", segment, label)
        s.configure("Segment.TRadiobutton", padding=(11, 5), foreground=MUTED, anchor="center", background=PAPER)
        s.map("Segment.TRadiobutton", foreground=[("disabled", MUTED), ("selected", CLAY_INK)])

    def icon(self, name):
        if name not in self.icons:
            img = tk.PhotoImage(master=self.root, width=26, height=19)
            for x1, y1, x2, y2 in _GLYPHS[name]:
                steps = max(abs(x2-x1), abs(y2-y1), 1)
                for n in range(steps + 1):
                    x = round(x1 + (x2-x1)*n/steps)
                    y = round(y1 + (y2-y1)*n/steps)
                    img.put("#735849", (x, y))
            self.icons[name] = img
        return self.icons[name]

    def card(self, parent, number, title, note=""):
        edge = ttk.Frame(parent, style="Surface.TFrame", padding=(16, 12, 16, 12))
        heading = ttk.Frame(edge)
        heading.pack(fill="x", pady=(0, 6))
        ttk.Label(heading, text=number, style="Index.TLabel").pack(side="left", padx=(0, 7))
        ttk.Label(heading, text=title, style="CardTitle.TLabel").pack(side="left")
        if note:
            ttk.Label(heading, text=note, style="Muted.TLabel").pack(side="right")
        body = ttk.Frame(edge)
        body.pack(fill="both", expand=True)
        return edge, body

    @staticmethod
    def text_panel(parent, **options):
        frame = ttk.Frame(parent)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        text = tk.Text(frame, **options)
        bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set)
        text.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        return frame, text


class ScrollForm:
    """Scroll only the setup canvas, including keyboard focus into off-screen fields."""
    def __init__(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.canvas = c = tk.Canvas(parent, highlightthickness=0, borderwidth=0, background=PAGE,
                                    yscrollincrement=16, takefocus=0)
        c.grid(row=0, column=0, sticky="nsew")
        self.bar = ttk.Scrollbar(parent, orient="vertical", command=c.yview)
        self.bar.grid(row=0, column=1, sticky="ns")
        c.configure(yscrollcommand=self.bar.set)
        self.body = ttk.Frame(c, style="Page.TFrame", padding=(1, 1, 2, 3))
        self.window = c.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self._layout)
        c.bind("<Configure>", self._layout)
        top = parent.winfo_toplevel()
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            top.bind(event, self._wheel, add="+")
        top.bind("<FocusIn>", self._focus, add="+")

    def _layout(self, _event):
        c = self.canvas
        width, height = c.winfo_width(), self.body.winfo_reqheight()
        c.itemconfigure(self.window, width=width)
        c.configure(scrollregion=(0, 0, width, height))
        if c.winfo_height() + 1 < height:
            self.bar.grid()
        else:
            self.bar.grid_remove()

    def _inside(self, widget):
        return widget == self.canvas or str(widget).startswith(str(self.body) + ".") or widget == self.body

    def _wheel(self, event):
        if not self.canvas.winfo_ismapped() or not self._inside(event.widget) or self.canvas.yview() == (0.,1.):
            return
        if event.widget.winfo_class() in ("Spinbox", "TSpinbox"):
            return
        direction = -1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(3 * direction, "units")
        return "break"

    def _focus(self, event):
        if not self.canvas.winfo_ismapped() or not self._inside(event.widget):
            return
        c, w = self.canvas, event.widget
        y = w.winfo_rooty() - c.winfo_rooty()
        delta = min(y, 0) or max(y + w.winfo_height() - c.winfo_height(), 0)
        if delta:
            c.yview_moveto((c.canvasy(0) + delta) / max(1,self.body.winfo_reqheight()))
