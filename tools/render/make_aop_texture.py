"""Top-surface maps for the IWR6843AOP (ALP0180A, 15 x 15 mm) package.

Geometry is measured from the package photo in TI SWRU546E (xWR6843AOPEVM user guide,
Figure 4-7 "AOP Antennas", 44.2 px/mm) and cross-checked against SWRS237C Figure 7-4
(lambda = 5 mm; RX on a lambda/2 grid, TX1-TX3 and TX3-TX2 at lambda; Pin A1 next to TX3).

Frame: top view, origin at the top-left package corner, u (mm) to the right, v (mm)
downward, Pin A1 at the bottom-left corner (u=0, v=15). This is the photo's own
orientation, and U300's footprint (rotated 90 deg) puts A1 at the same corner.
Outputs (3000 x 3000, 200 px/mm) into build/product-renders/aop-top/:
  color.png   sRGB albedo
  rough.png   linear roughness
  height.png  linear height (0.5 = mask surface)
  mark.png    linear laser-mark mask (drives coat removal)

Run: python3 tools/render/make_aop_texture.py   (needs Pillow)
"""
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

PKG = 15.0
PPM = 200          # output px/mm
SS = 2             # supersampling
R = PPM * SS
N = int(PKG * R)
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = str(REPO_ROOT / "build" / "product-renders" / "aop-top")
FONT = ("/System/Library/Fonts/HelveticaNeue.ttc", 7)  # Helvetica Neue Light

# --- measured features (mm, photo frame) ---------------------------------------
PATCH_W, PATCH_H = 1.60, 1.30  # 60 GHz patch: ~lambda_g/2 along the vertical feed axis
ANTENNAS = {
    "RX4": (7.26, 3.64), "RX2": (9.70, 3.64),
    "RX3": (7.26, 6.14), "RX1": (9.70, 6.14),
    "TX1": (2.30, 8.00), "TX3": (2.30, 12.97), "TX2": (7.26, 12.97),
}
# Buried copper regions that read as bluish tone through the mask.
TONAL = [
    (5.85, 0.28, 11.30, 2.30),
    (1.12, 5.08, 4.00, 6.66),
    (1.21, 9.42, 4.02, 10.92),
    (5.80, 7.45, 11.35, 11.08),
]
MARK_COL_X = 13.71        # centre of the two-line marking column
MARK_CAP = 0.73
# Production part (IWR6843ARQGALPR) drops the EVM sample's "X" prefix; the column
# keeps the photographed top end (ALP at v = 6.72) and 0.49 mm character pitch.
MAIN_TEXT = ("IWR6843 QG ALP", 6.72, 6.72 + 14 * 0.49)
LOT_TEXT = ("9CZD7V9", 1.04, 4.78)
SITE_TEXT = ("G1", 12.13, 1.35, 2.25, 0.66)   # text, centre u, v top, v bottom, cap
SITE_RULE = (12.82, 1.30, 2.30)
LOGO_BOX = (9.85, 12.55, 11.20, 14.20)
PIN1 = (4.64, 13.97, 0.57 / 2)
STROKE = 0.065

BASE_RGB = (27, 39, 32)
TONAL_RGB = (22, 39, 36)
PATCH_RGB = (36, 50, 36)
MARK_RGB = (150, 158, 152)
PIN1_RGB = (92, 100, 96)


def px(v):
    return int(round(v * R))


def rect(d, u0, v0, u1, v1, fill, radius=0.12):
    d.rounded_rectangle((px(u0), px(v0), px(u1), px(v1)), radius=px(radius), fill=fill)


def text_block(s, length_mm, cap_mm):
    """Laser-mark style: fixed character pitch, glyphs at natural width; rotated 90 deg CCW."""
    f = ImageFont.truetype(FONT[0], 400, index=FONT[1])
    hb = f.getbbox("H")
    scale = px(cap_mm) / (hb[3] - hb[1])
    pitch = px(length_mm) / len(s)
    base = int(px(cap_mm) * 1.1)
    im = Image.new("L", (px(length_mm), int(px(cap_mm) * 1.35)), 0)
    for i, ch in enumerate(s):
        if ch == " ":
            continue
        l, t, r, b = f.getbbox(ch)
        g = Image.new("L", (r - l + 20, b + 20), 0)
        ImageDraw.Draw(g).text((10 - l, 0), ch, font=f, fill=255)
        gw = min(g.width * scale, 0.78 * pitch)  # laser font is condensed to the pitch
        g = g.resize((max(1, int(gw)), max(1, int(g.height * scale))), Image.LANCZOS)
        im.paste(255, (int(i * pitch + pitch / 2 - g.width / 2), int(base - hb[3] * scale)), g)
    return im.transpose(Image.ROTATE_90)  # reads bottom -> top


def paste_mask(dst, src, cu, cv):
    dst.paste(255, (int(px(cu) - src.width / 2), int(px(cv) - src.height / 2)), src)


# Texas outline as (lon, lat), simplified; projected with cos(31 deg) for aspect.
TEXAS = [(-103.04, 36.50), (-100.00, 36.50), (-100.00, 34.56), (-99.2, 34.4), (-98.0, 34.1),
         (-97.0, 33.8), (-96.0, 33.85), (-95.0, 33.9), (-94.04, 33.55), (-94.04, 31.99),
         (-93.8, 31.5), (-93.55, 30.5), (-93.7, 29.9), (-94.7, 29.4), (-95.5, 28.8), (-96.4, 28.4),
         (-97.2, 27.7), (-97.4, 26.8), (-97.15, 25.95), (-97.5, 25.9), (-98.2, 26.1), (-99.1, 26.5),
         (-99.5, 27.5), (-100.3, 28.3), (-100.7, 29.1), (-101.4, 29.8), (-102.3, 29.9),
         (-102.7, 29.7), (-103.1, 29.0), (-103.8, 29.3), (-104.5, 29.7), (-104.7, 30.2),
         (-105.0, 30.7), (-106.0, 31.4), (-106.6, 31.8), (-106.6, 32.0), (-103.06, 32.0)]


def texas_logo(w_mm, h_mm):
    """TI logo (Texas with a 'ti' knockout), outline-only as laser engraved, upright."""
    W, H = px(w_mm), px(h_mm)
    m = px(STROKE) * 2  # margin so the outline is not clipped
    k = 0.857
    xs = [lon * k for lon, _ in TEXAS]
    ys = [lat for _, lat in TEXAS]
    sx, sy = (W - 1) / (max(xs) - min(xs)), (H - 1) / (max(ys) - min(ys))
    fill = Image.new("L", (W + 2 * m, H + 2 * m), 0)
    d = ImageDraw.Draw(fill)
    d.polygon([(m + (x - min(xs)) * sx, m + (max(ys) - y) * sy) for x, y in zip(xs, ys)], fill=255)
    f = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", int(H * 0.52), index=1)
    d.text((m + W * 0.36, m + H * 0.18), "ti", font=f, fill=0)
    k = max(3, px(STROKE) | 1)
    return ImageChops.subtract(fill.filter(ImageFilter.MaxFilter(k)), fill)


def main():
    os.makedirs(OUT, exist_ok=True)
    color = Image.new("RGB", (N, N), BASE_RGB)
    tonal = Image.new("L", (N, N), 0)
    patch = Image.new("L", (N, N), 0)
    mark = Image.new("L", (N, N), 0)
    pin1 = Image.new("L", (N, N), 0)

    td = ImageDraw.Draw(tonal)
    for box in TONAL:
        rect(td, *box, 255, radius=0.15)
    pd = ImageDraw.Draw(patch)
    for cu, cv in ANTENNAS.values():
        rect(pd, cu - PATCH_W / 2, cv - PATCH_H / 2, cu + PATCH_W / 2, cv + PATCH_H / 2, 255, radius=0.03)

    s, v0, v1 = MAIN_TEXT
    paste_mask(mark, text_block(s, v1 - v0, MARK_CAP), MARK_COL_X, (v0 + v1) / 2)
    s, v0, v1 = LOT_TEXT
    paste_mask(mark, text_block(s, v1 - v0, MARK_CAP), MARK_COL_X, (v0 + v1) / 2)
    s, cu, v0, v1, cap = SITE_TEXT
    paste_mask(mark, text_block(s, v1 - v0, cap), cu, (v0 + v1) / 2)
    md = ImageDraw.Draw(mark)
    u, a, b = SITE_RULE
    md.rectangle((px(u - STROKE / 2), px(a), px(u + STROKE / 2), px(b)), fill=255)
    u0, v0, u1, v1 = LOGO_BOX
    logo = texas_logo(v1 - v0, u1 - u0).transpose(Image.ROTATE_90)
    paste_mask(mark, logo, (u0 + u1) / 2, (v0 + v1) / 2)

    cu, cv, r = PIN1
    pd1 = ImageDraw.Draw(pin1)
    pd1.ellipse((px(cu - r), px(cv - r), px(cu + r), px(cv + r)), fill=255)
    hatch = Image.new("L", (N, N), 0)
    hd = ImageDraw.Draw(hatch)
    step = px(0.06)
    for k in range(px(cu - r) - px(2 * r), px(cu + r), step):
        hd.line((k, px(cv + r), k + px(2 * r), px(cv - r)), fill=255, width=max(1, px(0.02)))
    pin1_hatch = ImageChops.multiply(pin1, hatch)

    color.paste(TONAL_RGB, (0, 0), tonal)
    color.paste(PATCH_RGB, (0, 0), patch.filter(ImageFilter.GaussianBlur(px(0.01))))
    color.paste(PIN1_RGB, (0, 0), pin1)
    color.paste(tuple(min(255, c + 30) for c in PIN1_RGB), (0, 0), pin1_hatch)
    color.paste(MARK_RGB, (0, 0), mark)

    rough = Image.new("L", (N, N), 82)          # glossy LPI
    rough.paste(200, (0, 0), mark)              # ablated mask is matte
    rough.paste(215, (0, 0), pin1)

    # Height: mask over 15 um copper patches rises; laser marks and the pin-1 dimple sink.
    height = Image.new("L", (N, N), 128)
    height.paste(176, (0, 0), patch.filter(ImageFilter.GaussianBlur(px(0.02))))
    height.paste(110, (0, 0), mark)
    height.paste(70, (0, 0), pin1)
    height = height.filter(ImageFilter.GaussianBlur(SS))

    size = (int(PKG * PPM),) * 2
    for name, im in (("color", color), ("rough", rough), ("height", height), ("mark", mark)):
        im.resize(size, Image.LANCZOS).save(os.path.join(OUT, f"{name}.png"))
    print("wrote", OUT, size)


if __name__ == "__main__":
    main()
