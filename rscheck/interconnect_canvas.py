"""RS instance schematics drawn from verified port bindings. Native, paginated Tk."""
from __future__ import annotations

from collections import defaultdict
import tkinter as tk
from tkinter import ttk

from . import gui_style as style
from .interconnect import instance_wires, schematic_columns


def port_role(pin, kind):
    if kind == "member":
        return pin["interface_type"] + (" · 赋值源" if pin["direction"]=="output" else " · 赋值目标")
    if kind == "interface":
        if pin["modport"]:
            return ".".join(v for v in (pin["interface_type"], pin["modport"]) if v)
        # This is one endpoint for the whole bundle, not separate in/out pins.
        return (pin["interface_type"] or "interface") + "（整体接口）"
    return pin["direction"]


def port_text(pin, kind):
    return f"{pin['port']}  ·  {port_role(pin,kind)}"


class InterconnectCanvas(ttk.Frame):
    PAGE, PINS, WIRES = 12, 48, 24
    MODES = ("整组拓扑", "连接明细", "端口证据", "全部实例", "诊断")

    def __init__(self, parent, ui):
        super().__init__(parent)
        self.ui, self.graph = ui, None
        self.focus_mode = tk.StringVar(self, self.MODES[0])
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        combo = ttk.Combobox(bar, state="readonly", width=10, textvariable=self.focus_mode, values=self.MODES)
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._switch())
        self.prev = ttk.Button(bar, text="‹", width=3, command=lambda: self._move(-1))
        self.next = ttk.Button(bar, text="›", width=3, command=lambda: self._move(1))
        self.prev.pack(side="left")
        self.next.pack(side="left")
        self.large = ttk.Button(bar, text="大图", width=5, command=self._expand)
        self.large.pack(side="right")
        self.status = ttk.Label(bar, style="Muted.TLabel")
        self.status.pack(side="left", padx=4)
        self.canvas = tk.Canvas(self, bg=style.PAGE, highlightthickness=0, width=1, height=1)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        for orient, method, grid in (("horizontal", self.canvas.xview, (2, 0, "ew")),
                                     ("vertical", self.canvas.yview, (1, 1, "ns"))):
            scroll = ttk.Scrollbar(self, orient=orient, command=method)
            scroll.grid(row=grid[0], column=grid[1], sticky=grid[2])
            self.canvas.configure(**{("x" if orient == "horizontal" else "y") + "scrollcommand": scroll.set})
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.set_graph(None, "运行 RTL 检查后，选中合并区域内任一参与行。")

    def _expand(self):
        if self.graph is None:
            return
        win = tk.Toplevel(self)
        win.title("RS 例化互联 · " + self.graph["range"])
        win.geometry("1000x680")
        frame = InterconnectCanvas(win, self.ui)
        frame.large.pack_forget()
        frame.pack(fill="both", expand=True)
        frame.set_graph(self.graph, group_no=self.group_no)
        frame.focus_mode.set(self.mode)
        frame._switch()
        frame._move(self.page)

    def set_graph(self, graph, empty="", *, group_no=None):
        if graph is not None and graph is self.graph and group_no == self.group_no:
            return
        self.group_no = group_no
        self.graph, self.empty = graph, empty
        nodes, nets = (graph["nodes"], graph["nets"]) if graph else ([], [])
        self.by_name = {n["instance"]: n for n in nodes}
        self.wires, notes = instance_wires(nets)
        self.counts = [sum(w.kind==k and w.mode!="multidrop" for w in self.wires) for k in ("signal","interface")]
        self.member_count = sum(w.kind=="member" for w in self.wires)
        self.multi_count = sum(w.mode=="multidrop" for w in self.wires)
        self.issues = list(graph["diagnostics"] if graph else []) + [
            {"row": None, "instance": "", "message": msg} for msg in notes]
        used = {p["instance"] for wire in self.wires for p in wire.pins}
        self.isolated = [n for n in nodes if n["instance"] not in used]
        self.port_pages = [(n, i) for n in nets for i in range(0, len(n["pins"]), self.PINS)]
        # A huge design never creates a huge Tk canvas. Small groups get their
        # whole topology; larger ones page edges, including every multi-drop pin.
        self.sheets, batch = [], []
        for w in self.wires:
            if w.mode == "multidrop":
                if batch:
                    self.sheets.append(("pairs", batch))
                    batch = []
                self.sheets.extend(("bus", (w, i)) for i in range(0, len(w.pins), self.PINS))
            else:
                batch.append(w)
                if len(batch) == self.WIRES:
                    self.sheets.append(("pairs", batch))
                    batch = []
        if batch:
            self.sheets.append(("pairs", batch))
        if not self.sheets and graph:
            self.sheets.append(("pairs", []))
        self.details = [(w, i) for w in self.wires for i in range(0, len(w.pins), self.PINS)]
        self.focus_mode.set(self.MODES[0])
        self._switch()

    def _switch(self):
        self.mode, self.page = self.focus_mode.get(), 0
        self._draw()

    def _move(self, delta):
        self.page += delta
        self._draw()

    def _text(self, x, y, value, *, size=10, bold=False, color=style.INK, tag="label", anchor="nw"):
        return self.canvas.create_text(x, y, text=value, anchor=anchor, fill=color,
                                       font=self.ui.f(size, "bold" if bold else "normal"), tags=(tag,))

    def _width(self, value, size=10, bold=False):
        return int(self.tk.call("font", "measure", self.ui.f(size, "bold" if bold else "normal"), str(value)))

    def _box(self, x, y, w, h):
        # Rounded card; only RS instances get a box in the schematic.
        r = 9
        return self.canvas.create_polygon(x+r,y, x+w-r,y, x+w,y, x+w,y+r, x+w,y+h-r,
            x+w,y+h, x+w-r,y+h, x+r,y+h, x,y+h, x,y+h-r, x,y+r, x,y, x+r,y,
            smooth=True, splinesteps=12, fill=style.PAPER, outline=style.FIELD_LINE, tags=("instance_box",))

    def _meta(self, name):
        n = self.by_name[name]
        return f"{n['module']}   ·   行 {', '.join(map(str, n['rows']))}"

    @staticmethod
    def _color(kind):
        return {"signal":style.BLUE,"interface":style.CLAY_INK,"member":style.SUCCESS}[kind]

    def _member_pair(self, wires, names):
        """One physical interface socket per formal; align its member lanes even
        when replies go the opposite way. Arrows end at the actual assign LHS."""
        left, right = sorted(names, key=lambda n: (-sum(w.pins[0]["instance"]==n for w in wires), n))
        order = (left,right)
        pins = {n:[next(p for p in w.pins if p["instance"]==n) for w in wires] for n in order}
        headers = {n:list(dict.fromkeys(p["formal"]+" · "+port_role(p,"interface") for p in pins[n]))
                   for n in order}
        widths = {n:max([190,self._width(n,11,True)+26,self._width(self._meta(n),9)+26] +
                        [self._width(s,10)+26 for s in headers[n]] +
                        [self._width(p["port"],9)+28 for p in pins[n]]) for n in order}
        lane = max([214]+[self._width(w.label,9,True)+44 for w in wires])
        x0, x1 = 18+widths[left], 18+widths[left]+lane
        first_y=150+22*max(map(len,headers.values()))
        height=first_y-76+(len(wires)-1)*35+19
        for n in order:
            x=18 if n==left else x1
            self._box(x,76,widths[n],height)
            self._text(x+10,80,n,size=11,bold=True,tag="instance")
            self._text(x+10,102,self._meta(n),size=9,color=style.MUTED)
            for j,h in enumerate(headers[n]):
                self._text(x+10,126+j*22,h,color=style.CLAY_INK,tag="formal")
        for i,w in enumerate(wires):
            y=first_y+35*i
            start,end=(x0,x1) if w.pins[0]["instance"]==left else (x1,x0)
            self._wire((start,y,end,y),w)
            self._text((x0+x1)//2,y-3,w.label,anchor="s",size=9,bold=True,color=style.SUCCESS,tag="wire_name")
            for n,edge,anchor,dx in ((left,x0,"e",-10),(right,x1,"w",10)):
                self._text(edge+dx,y,pins[n][i]["port"],anchor=anchor,size=9,color=style.SUCCESS,tag="pin")
                self._dot(edge,y,style.SUCCESS)
        self._isolates(76+height+26)

    def _dot(self, x, y, color):
        self.canvas.create_oval(x-3, y-3, x+3, y+3, fill=color, outline="", tags=("terminal",))

    def _wire(self, coords, w, dash=None):
        cfg = {"dash": dash} if dash else {}
        return self.canvas.create_line(*coords, fill=self._color(w.kind), width=2,
            arrow="last" if w.mode == "directed" else "none", arrowshape=(10,12,5), joinstyle="round",
            tags=("wire", w.kind), **cfg)

    def _draw_pairs(self, wires):
        self._text(18, 8, "RS 例化互联", size=14, bold=True)
        counts = f"{self.counts[0]} 条信号连接   /   {self.counts[1]} 条接口绑定"
        if self.member_count:
            counts += f"   /   {self.member_count} 条成员连接"
        if self.multi_count:
            counts += f"   /   {self.multi_count} 组多端共绑"
        self._text(24+self._width("RS 例化互联",14,True),14,counts,size=9,color=style.MUTED)
        self._text(18, 38, "蓝：信号     棕：共享整束接口     绿：接口成员赋值     虚线：同网，方向不确定",
                   size=9, color=style.MUTED)
        if not wires:
            self._text(18, 80, "当前合并区没有可确认的组内 RS 对端。请查看「端口证据」或「诊断」。", color=style.MUTED)
            self._isolates(130)
            return
        names = {p["instance"] for w in wires for p in w.pins}
        if len(names)==2 and all(w.kind=="member" and w.mode=="directed" for w in wires):
            self._member_pair(wires,names)
            return
        columns = schematic_columns(wires)
        col = {name: i for i, names in enumerate(columns) for name in names}
        left, right = defaultdict(dict), defaultdict(dict)
        adjacent = [dict() for _ in columns]
        # Distinct formal ports receive distinct sockets. Fanout to the same
        # signal uses a common rail, never a link between two receiver modules.
        for w in wires:
            a, b = w.pins
            right[a["instance"]][(a["port"], w.kind)] = a
            left[b["instance"]][(b["port"], w.kind)] = b
            i = col[a["instance"]]
            if col[b["instance"]] == i+1:
                adjacent[i].setdefault((w.kind, w.identity, a["instance"], a["port"]), []).append(w)
        boxes, sockets, left_x, widths = {}, {}, [], []
        groups = []
        def measurements(name, side):
            return max([0] + [self._width(port_text(p, k), 9) for (_, k), p in side[name].items()])
        for names in columns:
            widths.append(max(max(178, self._width(n, 11, True)+26, self._width(self._meta(n), 9)+26,
                             measurements(n, left)+measurements(n, right)+36) for n in names))
        x, gap = 18, []
        for i, width in enumerate(widths):
            left_x.append(x)
            # Separate parallel signal and interface rails; names are on the
            # wire and never get a network box or a clickable network node.
            lane = max([208] + [self._width(legs[0].label, 9, True)+70 for legs in adjacent[i].values()])
            gap.append(lane + max(0, len(adjacent[i])-1)*16)
            x += width + gap[-1]
        height = {n: 50+18*max(len(left[n]),len(right[n]),1) for n in col}
        heights = [sum(height[n] for n in names)+12*(len(names)-1) for names in columns]
        extent = max(heights)
        for i, names in enumerate(columns):
            x, width = left_x[i], widths[i]
            y = 76+(extent-heights[i])//2
            for n in names:
                boxes[n] = (x, y, width, height[n])
                for side, table in (("l", left[n]), ("r", right[n])):
                    for j, (port, kind) in enumerate(table):
                        sockets[(side,n,port)] = (x if side=="l" else x+width, y+49+j*18)
                y += height[n]+12
        def ends(w):
            a, b = w.pins
            return sockets["r",a["instance"],a["port"]], sockets["l",b["instance"],b["port"]]
        for i, rails in enumerate(adjacent):
            for j, (key, legs) in enumerate(rails.items()):
                kind = key[0]
                (sx, sy), _ = ends(legs[0])
                # Parallel identities at the same RS use DIFFERENT tracks.
                rail_x = left_x[i] + widths[i] + gap[i]-32-j*16
                groups.append((sx+8, sy, legs[0].label, kind))
                heights_on_rail = {sy}
                for w in legs:
                    _, (tx, ty) = ends(w)
                    heights_on_rail.add(ty)
                    self._wire((sx,sy, rail_x,sy, rail_x,ty, tx,ty), w,
                               (5,3) if kind=="signal" and w.mode!="directed" else None)
                # Dots distinguish a real fanout junction from unrelated rails
                # crossing in the same gap. Leave plain elbows unmarked.
                for ty in sorted(heights_on_rail)[1:-1]:
                    self.canvas.create_oval(rail_x-3,ty-3,rail_x+3,ty+3,outline="",
                                            fill=self._color(kind),tags=("junction",))
        # Feedback and long-range links route outside the component boxes;
        # direction always comes from the pins, never their visual position.
        bottom = 76+extent
        bypass = [w for w in wires if col[w.pins[1]["instance"]] != col[w.pins[0]["instance"]]+1]
        for i,w in enumerate(bypass):
            (sx,sy),(tx,ty) = ends(w)
            y = bottom+28+i*24
            self._wire((sx,sy, sx+8,sy, sx+8,y, tx-8,y, tx-8,ty, tx,ty),w,
                       (5,3) if w.kind=="signal" and w.mode!="directed" else None)
            groups.append((min(sx,tx)+8,y,w.label,w.kind))
        for x,y,name,kind in groups:
            self._text(x, y-2, name, anchor="sw", color=self._color(kind), size=9, bold=True, tag="wire_name")
        for name, (x,y,width,h) in boxes.items():
            self._box(x,y,width,h)
            self._text(x+10,y+2,name, size=11,bold=True,tag="instance")
            self._text(x+10,y+24,self._meta(name),size=9,color=style.MUTED)
            for side, table in (("l",left[name]),("r",right[name])):
                for (port,kind),pin in table.items():
                    px,py = sockets[side,name,port]
                    self._text(px+(9 if side=="l" else -9),py,port_text(pin,kind),
                               anchor="w" if side=="l" else "e",color=self._color(kind),size=9,tag="pin")
                    self._dot(px,py,self._color(kind))
        self._isolates(bottom+len(bypass)*24+28)

    def _isolates(self, y):
        if not self.isolated:
            return
        self._text(18,y,f"未确认组内对端  ·  {len(self.isolated)} 个 RS",bold=True,color=style.MUTED)
        # The instance index always lists ALL names, including on large groups.
        for i,n in enumerate(self.isolated[:self.PAGE]):
            self._text(20,y+24+i*23,n["instance"],color=style.MUTED,tag="isolated")
        if len(self.isolated)>self.PAGE:
            self._text(20,y+24+self.PAGE*23,"其余见「全部实例」。",color=style.MUTED)

    def _draw_bus(self, wire, start):
        pins = wire.pins[start:start+self.PINS]
        color = self._color(wire.kind)
        self._text(16,10,wire.identity,bold=True,size=12,color=color,tag="wire_name")
        self._text(16,37,f"多端共绑 · 不指定一对一关系或方向   ·   端点 {start+1}–{start+len(pins)}/{len(wire.pins)}",
                   color=style.MUTED)
        dash = {"dash":(5,3)} if wire.kind=="signal" else {}
        self.canvas.create_line(28,85,28,85+(len(pins)-1)*66,fill=color,width=2,tags=("shared_bus",),**dash)
        for i,p in enumerate(pins):
            y=85+i*66
            name, port = p["instance"], port_text(p,wire.kind)
            self.canvas.create_line(28,y,72,y,fill=color,width=2,tags=("bus_branch",),**dash)
            self._dot(28,y,color)
            self._box(72,y-25,max(224,self._width(name,11,True)+22,self._width(port)+22),54)
            self._text(82,y-23,name,size=11,bold=True,tag="instance")
            self._text(82,y+1,port,color=color,tag="pin")
        self._isolates(85+len(pins)*66)

    def _draw_details(self, items):
        y=12
        for w,start in items:
            color = self._color(w.kind)
            mode = {"directed":"输出 → 输入", "binding":"无向绑定", "multidrop":"多端共绑"}[w.mode]
            canon = f"   ·   等价网络标识 {w.identity}" if w.kind=="signal" and w.label != w.identity else ""
            self._text(14,y,f"{w.kind}   ·   {w.label}   ·   {mode}{canon}",bold=True,color=color,tag="wire_name")
            y+=24
            if w.path:
                self._text(22,y,"上层 RTL 赋值路径："+" → ".join(w.path),color=color,tag="member_path")
                y+=22
            for p in w.pins[start:start+self.PINS]:
                role = ("命中行 "+", ".join(map(str,p["selected_by"]))) if p["selected_by"] else "同网对端"
                self._text(22,y,f"{p['instance']}.{p['port']}   ·   {port_role(p,w.kind)}   ·   {role}"
                           f"   ·   实接 {p['connection'] or '—'}",tag="pin")
                y+=21
            y+=12

    def _draw(self):
        c, g = self.canvas, self.graph
        c.delete("all")
        mode = self.MODES.index(self.mode)
        entries = [self.sheets,self.details,self.port_pages,list(self.by_name.values()) if mode==3 else [],self.issues][mode]
        size = (1,4,1,self.PAGE,self.PAGE)[mode]
        pages = max(1,(len(entries)+size-1)//size)
        self.page=max(0,min(self.page,pages-1))
        self.prev.state(["!disabled" if self.page else "disabled"])
        self.next.state(["!disabled" if self.page+1<pages else "disabled"])
        self.large.state(["!disabled" if g else "disabled"])
        group_index = f"合并组 {self.group_no[0]}/{self.group_no[1]} · " if self.group_no else ""
        self.status.configure(text=("" if not g else f"{group_index}{g['range']} · 组内页 {self.page+1}/{pages} · "
                                    f"{len(self.by_name)} 个 RS   ·   {len(self.issues)} 条诊断"))
        items=entries[self.page*size:(self.page+1)*size]
        if not items:
            msg = self.empty if not g else ("没有诊断。" if mode==4 else "当前合并区没有此类证据。")
            self._text(14,14,msg,color=style.MUTED)
        elif mode==0:
            kind, data = items[0]
            if kind=="pairs":
                self._draw_pairs(data)
            else:
                self._draw_bus(*data)
        elif mode==1:
            self._draw_details(items)
        elif mode==2:
            net, start = items[0]
            pins=net["pins"][start:start+self.PINS]
            self._text(14,10,net["identity"],bold=True,color=self._color(net["kind"]))
            self._text(14,33,f"{net['kind']}   ·   原始端口证据 {start+1}–{start+len(pins)}/{len(net['pins'])}"
                       + ("   ·   没有其他组内 RS" if not net["shared"] else ""),color=style.MUTED)
            for i,p in enumerate(pins):
                self._text(18,64+i*48,f"{p['instance']}.{p['port']}   ·   {port_role(p,net['kind'])}",bold=True,tag="pin")
                role = ("命中 Excel 行 "+", ".join(map(str,p["selected_by"]))) if p["selected_by"] else "同网对端"
                self._text(18,85+i*48,f"{role}   ·   实接 {p['connection'] or '—'}",color=style.MUTED)
        else:
            for i,e in enumerate(items):
                if mode==3:
                    self._text(14,12+i*46,e["instance"],bold=True,tag="instance")
                    self._text(18,34+i*46,self._meta(e["instance"]),color=style.MUTED)
                else:
                    self._text(14,12+i*46,f"行 {e['row']}   {e['instance']}" if e["row"] else "组内连线 / 设计库",bold=True)
                    self._text(18,34+i*46,e["message"],color=style.AMBER)
        bb=c.bbox("all")
        c.configure(scrollregion=(0,0,bb[2]+18,bb[3]+16) if bb else (0,0,1,1))
        c.xview_moveto(0)
        c.yview_moveto(0)
