#!/usr/bin/env python3
"""Regenerate publication drawings from the KiCad PCB (Python standard library only).

Run from any directory: python3 tools/board_svg.py
Coordinates in the drawings use a back-view datum: intersection of the nominal
straight top and left edges, +X right, +Y down. This is the PCB's top-right corner.
Footprint geometry is already flipped in KiCad's saved back-side footprints;
apply its rotation, then mirror the board X coordinate exactly once. Footprint
rule-area polygons in KiCad 10 are stored in board coordinates, not local ones.
"""
from __future__ import annotations

import html
import math
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import re

ROOT = Path(__file__).resolve().parents[1]
COLORS = {"Power": "#f5aaa0", "GND": "#d5dbe1", "UART": "#a9d5ef",
          "SPI": "#a9dfbe", "Control / boot": "#d1b8ea", "Sync / IRQ": "#f2d58d"}
INK = "#20343c"


def parse(text):
    """Read S-expressions without importing KiCad or relying on line layout."""
    root, stack = [], []
    current = root
    for match in re.finditer(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', text):
        token = match.group()
        if token == "(":
            child = []
            current.append(child)
            stack.append(current)
            current = child
        elif token == ")":
            if not stack:
                raise ValueError("Unbalanced closing parenthesis")
            current = stack.pop()
        elif token.startswith('"'):
            current.append(re.sub(r'\\([\\"])', r'\1', token[1:-1]))
        else:
            current.append(token)
    if stack or len(root) != 1:
        raise ValueError("Invalid PCB S-expression")
    return root[0]


def children(node, key):
    return [n for n in node if isinstance(n, list) and n and n[0] == key]


def one(node, key, default=None):
    found = children(node, key)
    return found[0] if found else default


def xy(node, key):
    return tuple(float(v) for v in one(node, key)[1:3])


def transform(point, footprint):
    x, y = point
    at = one(footprint, "at")
    angle = math.radians(float(at[3]) if len(at) > 3 else 0)
    return (float(at[1]) + x * math.cos(angle) + y * math.sin(angle),
            float(at[2]) - x * math.sin(angle) + y * math.cos(angle))


def bounds(points):
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def arc_circle(a, b, c):
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2 * (ax * (by-cy) + bx * (cy-ay) + cx * (ay-by))
    ux = ((ax*ax+ay*ay)*(by-cy)+(bx*bx+by*by)*(cy-ay)+(cx*cx+cy*cy)*(ay-by))/d
    uy = ((ax*ax+ay*ay)*(cx-bx)+(bx*bx+by*by)*(ax-cx)+(cx*cx+cy*cy)*(bx-ax))/d
    return (ux, uy), math.hypot(ax-ux, ay-uy)


class Board:
    def __init__(self, path):
        self.tree = parse(path.read_text())
        self.edges = [n for n in self.tree if isinstance(n, list) and n[0] in ("gr_line", "gr_arc")
                      and one(n, "layer")[1] == "Edge.Cuts"]
        self.box = bounds([xy(n, key) for n in self.edges for key in ("start", "end")])
        self.x0, self.y0, self.x1, self.y1 = self.box
        self.width, self.height = self.x1-self.x0, self.y1-self.y0
        self.fp = {one(n, "property")[2]: n for n in children(self.tree, "footprint")
                   if one(n, "property") and one(n, "property")[1] == "Reference"}
        required = {"J400", "J401", "J301", "J200", "U300", "H1", "H3", "H4"}
        if not required <= self.fp.keys():
            raise ValueError(f"Missing footprints: {required-self.fp.keys()}")
        self.notch = sorted([n for n in self.edges if n[0] == "gr_line"
                             and abs(xy(n, "start")[0]-xy(n, "end")[0]) > 0.01
                             and abs(xy(n, "start")[1]-xy(n, "end")[1]) > 0.01],
                            key=lambda n: min(xy(n, "start")[0], xy(n, "end")[0]))
        if len(self.notch) != 2:
            raise ValueError("Expected two notch slopes")
        self.notch_points = sorted(set(xy(n, k) for n in self.notch for k in ("start", "end")))

    def back(self, p):
        return self.x1-p[0], p[1]-self.y0

    def center(self, ref):
        return self.back(xy(self.fp[ref], "at"))

    def pads(self, ref):
        return children(self.fp[ref], "pad")

    def pad(self, ref, number):
        return next(p for p in self.pads(ref) if p[1] == str(number))

    def pad_center(self, ref, number):
        return self.back(transform(xy(self.pad(ref, number), "at"), self.fp[ref]))

    def socket_contact(self, ref, number):
        """Socket contacts have 2.54-mm row spacing; land spacing is wider."""
        px, py = xy(self.pad(ref, number), "at")
        return self.back(transform((math.copysign(1.27, px), py), self.fp[ref]))

    def net(self, ref, number):
        n = one(self.pad(ref, number), "net", ["net", "NC"])
        return n[-1]

    def geometry(self, ref, layer):
        points = []
        for n in self.fp[ref]:
            if isinstance(n, list) and n[0] in ("fp_line", "fp_rect", "fp_poly") and one(n, "layer")[1] == layer:
                if n[0] == "fp_poly":
                    local = [tuple(map(float, p[1:3])) for p in children(one(n, "pts"), "xy")]
                else:
                    a, b = xy(n, "start"), xy(n, "end")
                    local = [a, b] if n[0] == "fp_line" else [a, (a[0], b[1]), b, (b[0], a[1])]
                points.extend(self.back(transform(p, self.fp[ref])) for p in local)
        if not points:
            raise ValueError(f"No {layer} body geometry for {ref}")
        return bounds(points)



def mm(value):
    # Strip floating-point rotation noise before conventional engineering rounding.
    return str(Decimal(str(round(value, 6))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fmt(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


class SVG:
    def __init__(self, width, height, title):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
                      f'<title id="title">{html.escape(title)}</title>',
                      '<desc id="desc">Generated from IWR-AOP-MODULE.kicad_pcb. Back face viewed directly; X is mirrored from PCB coordinates. All dimensions in millimetres.</desc>',
                      '<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto-start-reverse"><path d="M 0 0 L 7 3.5 L 0 7 Z" fill="#526873"/></marker></defs>',
                      '<style>text{font-family:Inter,Helvetica,Arial,sans-serif;fill:#20343c} .dim{stroke:#526873;stroke-width:1.3;fill:none} .ext{stroke:#8b9aa1;stroke-width:1;fill:none} .white{fill:#eff8f3}</style>',
                      f'<rect width="{width}" height="{height}" fill="#fafcfc"/>']

    def add(self, markup):
        self.parts.append(markup)

    def text(self, x, y, value, size=18, fill=INK, anchor="start", weight=400, **attrs):
        extras = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
        self.add(f'<text x="{fmt(x)}" y="{fmt(y)}" style="fill:{fill}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" {extras}>{html.escape(str(value))}</text>')

    def line(self, a, b, color="#526873", width=1.3, extra=""):
        self.add(f'<path d="M{fmt(a[0])},{fmt(a[1])} L{fmt(b[0])},{fmt(b[1])}" fill="none" stroke="{color}" stroke-width="{width}" {extra}/>')

    def rect(self, x, y, w, h, fill, stroke="none", radius=0, extra=""):
        self.add(f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" rx="{radius}" fill="{fill}" stroke="{stroke}" {extra}/>')

    def circle(self, x, y, r, fill, stroke="none", extra=""):
        self.add(f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(r)}" fill="{fill}" stroke="{stroke}" {extra}/>')

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([*self.parts, "</svg>", ""]), encoding="utf-8")


class Drawing:
    def __init__(self, board, svg, origin, scale):
        self.b, self.svg, self.origin, self.scale = board, svg, origin, scale

    def point(self, p):
        return self.origin[0]+p[0]*self.scale, self.origin[1]+p[1]*self.scale

    def pcbpoint(self, p):
        return self.point(self.b.back(p))

    def base(self, refs=True):
        b, s = self.b, self.svg
        remaining = b.edges[:]
        first = remaining.pop(0)
        start, end = xy(first, "start"), xy(first, "end")
        ordered = [(first, False)]
        while remaining:
            for i, n in enumerate(remaining):
                if math.dist(end, xy(n, "start")) < 0.001:
                    reverse = False; break
                if math.dist(end, xy(n, "end")) < 0.001:
                    reverse = True; break
            else:
                raise ValueError("Edge.Cuts does not form one connected outline")
            ordered.append((remaining.pop(i), reverse))
            end = xy(n, "start" if reverse else "end")
        if math.dist(end, start) > 0.001:
            raise ValueError("Edge.Cuts outline is not closed")
        x, y = self.pcbpoint(start)
        commands = [f"M{fmt(x)},{fmt(y)}"]
        for n, reverse in ordered:
            a, c = xy(n, "start"), xy(n, "end")
            if reverse: a, c = c, a
            x, y = self.pcbpoint(c)
            if n[0] == "gr_line":
                commands.append(f"L{fmt(x)},{fmt(y)}")
            else:
                mid = xy(n, "mid")
                _, radius = arc_circle(a, mid, c)
                aa, mm, cc = map(self.pcbpoint, (a, mid, c))
                cross = (mm[0]-aa[0])*(cc[1]-mm[1])-(mm[1]-aa[1])*(cc[0]-mm[0])
                commands.append(f"A{fmt(radius*self.scale)},{fmt(radius*self.scale)} 0 0 {int(cross>0)} {fmt(x)},{fmt(y)}")
        s.add(f'<path d="{" ".join(commands)} Z" fill="#145444" stroke="#0c3e32" stroke-width="2"/>')
        # A dashed silhouette denotes the radar on the opposite (front) face.
        box = b.geometry("U300", "F.Fab")
        x, y = self.point(box[:2])
        s.rect(x, y, (box[2]-box[0])*self.scale, (box[3]-box[1])*self.scale,
               "#246653", "#9cc3ac", 2, 'stroke-dasharray="5 4"')
        s.text(x+(box[2]-box[0])*self.scale/2, y+24, "U300 · FRONT", 15, "#d7e8dc", "middle")
        for ref in ("J400", "J401", "J301", "J200"):
            f = b.fp[ref]
            layer = one(f, "layer")[1]
            if layer != "B.Cu":
                raise ValueError(f"{ref} is not on the back")
            for p in b.pads(ref):
                if p[2] == "np_thru_hole": continue
                center = self.pcbpoint(transform(xy(p, "at"), f))
                w, h = xy(p, "size")
                at = one(p, "at")
                # Pad angle is absolute in the saved board, unlike its local position.
                angle = float(at[3]) if len(at) > 3 else 0
                s.rect(center[0]-w*self.scale/2, center[1]-h*self.scale/2,
                       w*self.scale, h*self.scale, "#d8b663", radius=1,
                       extra=f'transform="rotate({fmt(angle)} {fmt(center[0])} {fmt(center[1])})"')
            box = b.geometry(ref, "B.Fab")
            x, y = self.point(box[:2]); w, h = (box[2]-box[0])*self.scale, (box[3]-box[1])*self.scale
            s.rect(x, y, w, h, "#192a2b" if ref != "J200" else "#55656b", "#738b82", 3)
            if ref in ("J400", "J401"):
                for pad in b.pads(ref):
                    # Contact openings use the 2.54-mm mating row spacing, not SMD-land row spacing.
                    p = self.point(b.socket_contact(ref, pad[1]))
                    s.rect(p[0]-3.2, p[1]-3.2, 6.4, 6.4, "#d8b663", radius=1)
                cx, cy = self.point(b.center(ref))
                if refs:
                    s.text(cx, cy+5, ref, 14, "#e5eee6", "middle", transform=f"rotate(-90 {fmt(cx)} {fmt(cy)})")
            elif ref == "J301":
                if refs:
                    s.text(x+w/2, y+h/2+5, ref, 14, "#e5eee6", "middle")
            else:
                s.rect(x+7, y+h-8, w-14, 5, "#15282d", radius=2)
                s.text(x+w/2, y+h/2+4, "USB-C", 14, "#ffffff", "middle")
            if ref != "J200":
                pin1 = b.socket_contact(ref, 1) if ref in ("J400", "J401") else b.pad_center(ref, 1)
                px, py = self.point(pin1)
                s.circle(px, py, 5, "none", "#fff3b2", 'stroke-width="2"')
                s.text(px, py-9, "1", 13, "#fff3b2", "middle", 700)
        for ref in ("H1", "H3", "H4"):
            p = next(p for p in b.pads(ref) if p[2] == "np_thru_hole")
            x, y = self.pcbpoint(transform(xy(p, "at"), b.fp[ref]))
            drill = float(one(p, "drill")[1])
            s.circle(x, y, drill*self.scale/2+2, "#a5bbad")
            s.circle(x, y, drill*self.scale/2, "#fafcfc", "#0c3e32")

    def horizontal(self, x1, x2, y, source_y, label):
        s = self.svg
        a, z = self.point((x1, y)), self.point((x2, y))
        for x in (x1, x2): s.line(self.point((x, source_y)), self.point((x, y-0.6)), "#8b9aa1", 1)
        s.line(a, z, extra='marker-start="url(#arrow)" marker-end="url(#arrow)"')
        s.text((a[0]+z[0])/2, a[1]-8, label, 18, anchor="middle", weight=500)

    def vertical(self, y1, y2, x, source_x, label):
        s = self.svg
        a, z = self.point((x, y1)), self.point((x, y2))
        for y in (y1, y2): s.line(self.point((source_x, y)), self.point((x+0.6, y)), "#8b9aa1", 1)
        s.line(a, z, extra='marker-start="url(#arrow)" marker-end="url(#arrow)"')
        tx, ty = a[0]-10, (a[1]+z[1])/2
        s.text(tx, ty, label, 18, anchor="middle", weight=500, transform=f"rotate(-90 {fmt(tx)} {fmt(ty)})")


def signal(net):
    short = net.rsplit("/", 1)[-1]
    names = {"PWR_IN": "5V IN", "AOP_UART_TX": "UART TX",
             "HOST_UART_TX": "UART RX", "HOST_SYNC_IN": "SYNC IN", "AOP_SOP2": "SOP2",
             "AOP_NERROR_OUT": "nERROR", "HOST_DISABLE": "DISABLE", "HOST_NRESET": "nRESET",
             "HOST_SPI_CLK": "SPI CLK", "HOST_SPI_MOSI": "SPI MOSI", "AOP_SPI_MISO": "SPI MISO",
             "HOST_SPI_CS_N": "SPI nCS", "AOP_HOST_INTR": "HOST IRQ", "FRAME_START": "FRAME START",
             "AOP_DATA_TX": "DATA TX"}
    label = names.get(short, short)
    if short == "GND": category = "GND"
    elif short == "PWR_IN": category = "Power"
    elif "UART" in short or "DATA_TX" in short: category = "UART"
    elif "SPI" in short: category = "SPI"
    elif short in ("HOST_SYNC_IN", "FRAME_START", "AOP_HOST_INTR"): category = "Sync / IRQ"
    else: category = "Control / boot"
    return label, category


def pinout(board):
    width, pad_x, top = 1500, 24, 92
    # Label geometry in board millimetres so pills scale with the board drawing.
    pill_w, pill_h, gap, font = 19.3, 2.25, 1.55, 1.9
    sockets = ("J400", "J401")
    label_x = []
    for ref in sockets:
        cx = board.center(ref)[0]
        for p in board.pads(ref):
            px = board.pad_center(ref, p[1])[0]
            label_x.append(px-gap-pill_w if px < cx else px+gap+pill_w)
    left, right = min(0, *label_x), max(board.width, *label_x)
    scale = (width-2*pad_x)/(right-left)
    legend_y = top+board.height*scale+44
    s = SVG(width, legend_y+58, "IWR-AOP-MODULE back view pinout")
    s.text(width-pad_x, 40, "3.3 V logic, not 5 V tolerant", 20, anchor="end")
    d = Drawing(board, s, (pad_x-left*scale, top), scale)
    d.base()
    hx, hy = d.point(board.center("J301"))
    s.text(hx, hy-6.8*scale, "Cortex-10 JTAG · 1.27 mm", 18, anchor="middle", weight=600)
    w, h, g, f = pill_w*scale, pill_h*scale, gap*scale, font*scale
    for ref in sockets:
        cx, cy = d.point(board.center(ref))
        for pad in board.pads(ref):
            pin = int(pad[1])
            label, category = signal(board.net(ref, pin))
            px, py = d.point(board.pad_center(ref, pin))
            x = px-w-g if px < cx else px+g
            # Both physical rows get their own adjacent labels, at the exact pad Y.
            s.line((px, py), (x+w if px < cx else x, py), "#b7d2c4", 1.2)
            s.rect(x, py-h/2, w, h, COLORS[category], radius=6)
            s.text(x+0.5*f, py+0.36*f, f"{pin}  {label}", f, weight=500)
            s.circle(px, py, 2.5, COLORS[category], "#234a40")
    widths = {"Power": 125, "GND": 110, "UART": 115, "SPI": 105, "Control / boot": 220, "Sync / IRQ": 180}
    x = (width-sum(widths.values())-18*(len(widths)-1))/2
    for category, color in COLORS.items():
        s.rect(x, legend_y, widths[category], 32, color, radius=8)
        s.text(x+widths[category]/2, legend_y+22, category, 18, anchor="middle", weight=500)
        x += widths[category]+18
    s.save(ROOT / "docs/images/pinout.svg")




def dimensions(board):
    """Centre-referenced, minimally defined: every feature is symmetric about the board centrelines."""
    b = board
    cx, cy = b.width/2, b.height/2
    h1, h3, h4 = (b.center(r) for r in ("H1", "H3", "H4"))
    j400, j401 = b.center("J400"), b.center("J401")
    notch = sorted(b.back(p) for p in b.notch_points)
    opening = [p for p in notch if abs(p[1]) < 0.01]
    floor = [p for p in notch if abs(p[1]) >= 0.01]
    # The drawing states symmetric dimensions only; refuse to draw them if the board stops being symmetric.
    checks = [h3[0]+h4[0]-2*cx, h1[1]+h4[1]-2*cy, h1[0]-h4[0], j400[0]-h3[0], j401[0]-h4[0],
              j400[1]-cy, j401[1]-cy, opening[0][0]+opening[1][0]-2*cx, floor[0][0]+floor[1][0]-2*cx]
    if max(map(abs, checks)) > 0.001:
        raise ValueError("Board is no longer symmetric about its centre; dimension scheme needs revisiting")
    s = SVG(1240, 650, "IWR-AOP-MODULE back view dimensions")
    d = Drawing(b, s, (175, 168), 11)
    d.base(refs=False)
    # Centrelines are the datums; everything is dimensioned symmetrically about them.
    centreline = 'stroke-dasharray="14 4 3 4"'
    for a, z in (((cx, -3), (cx, b.height+3)), ((-3, cy), (b.width+3, cy))):
        s.line(d.point(a), d.point(z), "#7c8f97", 1.2, centreline)
    s.text(*d.point((cx+0.7, -0.9)), "℄", 22, weight=600)
    s.text(*d.point((-3.4, cy+0.6)), "℄", 22, anchor="end", weight=600)
    # Sockets share the hole columns and sit on the horizontal centreline: no dimensions of their own.
    for top, bottom, x in ((cy-8.2, h3[1], h3[0]), (h1[1], h4[1], h4[0])):
        s.line(d.point((x, top)), d.point((x, bottom)), "#c7d7ce", 1, centreline)
    for ref in ("H1", "H3", "H4"):
        x, y = d.point(b.center(ref))
        s.line((x-16, y), (x+16, y), "#c7d7ce", 1)
        s.line((x, y-16), (x, y+16), "#c7d7ce", 1)
    d.horizontal(0, b.width, -11, 0, mm(b.width))
    d.horizontal(opening[0][0], opening[1][0], -5, 0, mm(opening[1][0]-opening[0][0]))
    d.horizontal(floor[0][0], floor[1][0], floor[0][1]-4.5, floor[0][1], mm(floor[1][0]-floor[0][0]))
    d.horizontal(h3[0], h4[0], b.height+7, h4[1], mm(h4[0]-h3[0]))
    d.vertical(0, b.height, -8, 0, mm(b.height))
    d.vertical(h1[1], h4[1], b.width+7, h4[0], mm(h4[1]-h1[1]))
    # Notch floor below the horizontal centreline: too short for an inline label.
    a, z = d.point((cx+3, cy)), d.point((cx+3, floor[0][1]))
    s.line(a, z, extra='marker-start="url(#arrow)" marker-end="url(#arrow)"')
    s.text(a[0]+8, (a[1]+z[1])/2+6, mm(floor[0][1]-cy), 18, weight=500)
    p1, p3 = b.socket_contact("J401", 1), b.socket_contact("J401", 3)
    dim_x = p1[0]-7
    start, end = d.point((dim_x, p1[1])), d.point((dim_x, p3[1]))
    for contact in (p1, p3):
        s.line(d.point(contact), d.point((dim_x-0.7, contact[1])), "#bfd6c7", 1)
    s.line(start, end, "#bfd6c7", extra='marker-start="url(#arrow)" marker-end="url(#arrow)"')
    s.text(start[0]-10, (start[1]+end[1])/2+5, f"{mm(math.dist(p1, p3))} pitch", 18, "#eff8f3", "end")
    radius = arc_circle(*(xy(next(n for n in b.edges if n[0] == "gr_arc"), k)
                          for k in ("start", "mid", "end")))[1]
    s.line(d.point((b.width-0.9, b.height-0.9)), d.point((b.width+3, b.height+3.5)), "#8b9aa1", 1)
    s.text(*d.point((b.width+3.4, b.height+4.1)), f"4× R{mm(radius)}", 18, weight=500)
    hole = next(p for p in b.pads("H3") if p[2] == "np_thru_hole")
    edge = (h3[0]-1.2, h3[1]+1.2)
    s.line(d.point(edge), d.point((-3, b.height+3.5)), "#8b9aa1", 1)
    s.text(*d.point((-3.4, b.height+4.1)), f"3× ⌀{mm(float(one(hole, 'drill')[1]))}", 18, anchor="end", weight=500)
    s.save(ROOT / "docs/images/dimensions.svg")




def main():
    board = Board(ROOT / "IWR-AOP-MODULE.kicad_pcb")
    pinout(board)
    dimensions(board)
    print("Generated docs/images/pinout.svg and docs/images/dimensions.svg (back view)")
    print(f"Outline {board.width:.2f} × {board.height:.2f} mm")
    for ref in ("H1", "H3", "H4", "J400", "J401", "J301", "J200", "U300"):
        f = board.fp[ref]
        at = one(f, "at")
        print(f"{ref}: PCB ({float(at[1]):.6f}, {float(at[2]):.6f}), rotation {at[3] if len(at)>3 else '0'}°, {one(f, 'layer')[1]}")
    for ref in ("J400", "J401", "J301"):
        print(f"{ref}: " + "; ".join(f"{p[1]}={board.net(ref, p[1])}" for p in board.pads(ref)))


if __name__ == "__main__":
    main()
