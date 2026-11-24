"""Small antialiased, stretchable ttk skins using only Tk and the standard library."""
from __future__ import annotations

from functools import lru_cache
from math import hypot
import tkinter as tk


@lru_cache(maxsize=64)
def _coverage(radius, stroke):
    # Tk tiles the centre (and edge strips), not GPU-stretches it. A one-pixel
    # centre turns one large card into hundreds of thousands of native blits.
    size = 2 * radius + TILE
    result = []
    for y in range(size):
        for x in range(size):
            dx = max(radius - x - .5, x + .5 - (size - radius), 0.)
            dy = max(radius - y - .5, y + .5 - (size - radius), 0.)
            # Signed distance to the boundary, including the straight cardinal edges.
            depth = min(radius - hypot(dx, dy), x + .5, y + .5, size - x - .5, size - y - .5)
            outer = min(1., max(0., depth + .5))
            inner = min(1., max(0., depth - stroke + .5))
            result.append((outer, inner))
    return result


def _rgb(color):
    return tuple(bytes.fromhex(color.lstrip("#")))


TILE = 48


class RoundedElements:
    """One image cache per Tcl interpreter. Resizing never redraws in Python."""

    def __init__(self, root, style):
        self.root = root
        self.style = style
        self.scale = min(2.5, max(.85, float(root.tk.call("tk", "scaling")) * .75))
        self.images = {}

    def pixels(self, value):
        return max(1, round(value * self.scale))

    def photo(self, radius, outside, inside, outline, stroke=1):
        key = radius, outside, inside, outline, stroke
        if key not in self.images:
            bg, fill, line = map(_rgb, (outside, inside, outline))
            size = 2 * radius + TILE
            data = bytearray()
            for outer, inner in _coverage(radius, stroke):
                if inner == 1:
                    data.extend(fill)
                elif outer == 0:
                    data.extend(bg)
                else:
                    data.extend(round(a * (1 - outer) + b * (outer - inner) + c * inner)
                                for a, b, c in zip(bg, line, fill))
            ppm = f"P6\n{size} {size}\n255\n".encode("ascii") + data
            self.images[key] = tk.PhotoImage(master=self.root, data=ppm, format="PPM")
        return self.images[key]

    def element(self, name, radius, outside, fill, outline, states=()):
        """State entries: ('focus' or ('selected','focus'), fill, outline, stroke)."""
        radius = self.pixels(radius)
        photo = lambda bg, line, width: self.photo(radius, outside, bg, line, self.pixels(width))
        specs = []
        for state, bg, line, width in states:
            flags = (state,) if isinstance(state, str) else state
            specs.append((*flags, photo(bg, line, width)))
        element = "RsPaper." + name
        self.style.element_create(element, "image", photo(fill, outline, 1), *specs,
                                  border=radius, padding=0, width=2*radius+1,
                                  height=2*radius+1, sticky="nsew")
        return element


def skin(style, name, element, content=None):
    """Put a nine-slice behind the native label, textarea, arrow, or frame."""
    node = {"sticky": "nsew"}
    if content:
        node["children"] = content
    style.layout(name, [(element, node)])
