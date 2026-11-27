"""Tk editor for explicit upstream driver port mappings."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk, messagebox

from .config import parse_trace_cells
from .model import ConfigError, RsCheckError


class TraceRuleEditor(tk.Toplevel):
    def __init__(self, parent, rules, on_save):
        super().__init__(parent)
        self.title("时钟 / 复位反追 · 中间模块端口规则")
        self.transient(parent)
        self.on_save = on_save
        self.rows = rules
        self.selected_key = None
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="从命中的输出，跳到本模块列出的输入继续追 driver。未登记的输出不会展开。").pack(anchor="w")
        listing = ttk.Frame(body)
        listing.pack(fill="both", expand=True, pady=10)
        self.tree = ttk.Treeview(listing, columns=("kind", "module", "output", "inputs"), show="headings",
                                 selectmode="browse", height=8)
        for name, title, width in zip(self.tree["columns"], ("类别", "模块定义名", "命中的输出", "继续追的输入"), (70, 165, 135, 200)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=55)
        scroller = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroller.set)
        scroller.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        form = ttk.Frame(body)
        form.pack(fill="x")
        self.kind, self.module, self.output, self.inputs = (tk.StringVar(self) for _ in range(4))
        self.kind.set("DIV")
        for i, (label, var) in enumerate(zip(("类别", "模块定义名", "输出端口", "输入端口（多个用逗号隔开）"),
                                             (self.kind, self.module, self.output, self.inputs))):
            ttk.Label(form, text=label).grid(row=0, column=i, sticky="w")
            if i == 0:
                widget = ttk.Combobox(form, values=("DIV", "GATE", "OCC"), state="readonly", width=6, textvariable=var)
            else:
                widget = ttk.Entry(form, textvariable=var, width=17 if i < 3 else 25)
                form.columnconfigure(i, weight=1)
            widget.grid(row=1, column=i, sticky="ew", padx=(0, 5))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        for title, fn in (("新建", self.clear), ("应用此条", self.apply_row), ("删除选中", self.remove)):
            ttk.Button(buttons, text=title, command=fn).pack(side="left", padx=(0, 5))
        ttk.Button(buttons, text="保存规则库", command=self.save).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=5)
        self.refresh()
        self.grab_set()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(i), values=(r.kind, r.module, r.output, ", ".join(r.inputs)))

    def select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        r = self.rows[int(selection[0])]
        self.selected_key = (r.module, r.output)
        self.kind.set(r.kind)
        self.module.set(r.module)
        self.output.set(r.output)
        self.inputs.set(", ".join(r.inputs))

    def clear(self):
        self.tree.selection_remove(*self.tree.selection())
        self.selected_key = None
        self.module.set("")
        self.output.set("")
        self.inputs.set("")

    def apply_row(self):
        entry = dict(kind=self.kind.get(), module=self.module.get().strip(), output=self.output.get().strip(),
                     inputs=[s for s in re.split(r"[,，;；\s]+", self.inputs.get().strip()) if s])
        retained = [r.as_dict() for r in self.rows if (r.module, r.output) != self.selected_key]
        try:
            self.rows = parse_trace_cells([*retained, entry])
        except ConfigError as exc:
            messagebox.showerror("端口规则无效", str(exc), parent=self)
            return False
        self.clear()
        self.refresh()
        return True

    def remove(self):
        if self.selected_key is not None:
            self.rows = tuple(r for r in self.rows if (r.module, r.output) != self.selected_key)
            self.clear()
            self.refresh()

    def save(self):
        if any(v.get().strip() for v in (self.module, self.output, self.inputs)) and not self.apply_row():
            return
        try:
            self.on_save(self.rows)
        except (RsCheckError, OSError, ValueError) as exc:
            messagebox.showerror("未保存规则库", str(exc), parent=self)
            return
        self.destroy()
