"""JLC placement rotation for each part, solved against JLC's own footprint for that part.

Two things decide the CPL rotation JLC needs; getting either wrong has rotated parts:

1. JLC places its *own* footprint for each LCSC part (the LCSC/EasyEDA library one), not ours.
   Those footprints have no common zero orientation: the EasyEDA title records where pin 1 is
   (``...-BL`` bottom-left, ``-BR``, ``-TL``), while KiCad's IPC-7351 footprints put pin 1
   top-left. The offset therefore belongs to the LCSC part, not to a footprint name.

2. Bottom-side parts. JLC applies a bottom rotation counter-clockwise *as seen looking at the
   bottom face*. KiCad's orientation for a flipped footprint doesn't map onto that with one
   formula, so no angle arithmetic is used at all.

For every part, JLC's pads (fetched by LCSC number) are fitted directly onto the part's placed
pads on the board. Bottom pads are mirrored left-right first, to the view from the bottom. The
fit is by pin number, or by land pattern plus the pin-1 pad where the libraries number pins
differently. The rotation that makes them coincide is the CPL rotation, so JLC's pin 1 (and its
package dot) lands on our pin 1.

Checked against JLC's placement preview for this board (2 Oct 2026): package dots on silkscreen
pin-1 marks for U117, U200 and U301; pads for J200, D200/D201, Y300 and the front-side parts.
Pads alone can't confirm parts whose land pattern is nearly 180-degree symmetric (SOT-583,
QFN-24, SOIC-8, LP87524's RNF0026C), so confirm those by the package dot.

Two-terminal parts are fitted against our own library footprint, assuming JLC's has pad 1 at the
left. They place identically either way round.

JLC footprints are cached by LCSC number in ``jlc-footprints.json`` so builds are reproducible
offline; a part not in the cache is fetched from the EasyEDA API once and added.
"""
import json
import math
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import pcbnew

CACHE = Path(__file__).resolve().parent / 'jlc-footprints.json'
STOCK = Path(os.environ.get('KICAD10_FOOTPRINT_DIR', '/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints'))
TOLERANCE_MM = 0.6  # JLC and KiCad land patterns differ slightly; a wrong angle misses by > 1 mm


def _centroid(points):
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def _rotate(p, degrees):
    """Counter-clockwise as seen on screen (KiCad and EasyEDA both have y pointing down)."""
    a = math.radians(degrees)
    return (p[0] * math.cos(a) + p[1] * math.sin(a), -p[0] * math.sin(a) + p[1] * math.cos(a))


class JlcFootprints:
    """JLC's footprint for an LCSC part: its EasyEDA title and pads as [number, x_mm, y_mm]."""

    def __init__(self):
        self.data = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    def get(self, lcsc):
        if lcsc not in self.data:
            url = f'https://easyeda.com/api/products/{lcsc}/components?version=6.4.19.5'
            for attempt in range(4):  # the API rate-limits bursts with a temporary 403
                time.sleep(2 + 15 * attempt)
                raw = subprocess.run(['curl', '-sf', '--max-time', '20', url], capture_output=True).stdout
                try:
                    package = json.loads(raw)['result']['packageDetail']
                    break
                except (ValueError, KeyError, TypeError):
                    package = None
            if package is None:
                raise SystemExit(f'{lcsc}: could not fetch JLC footprint from EasyEDA (rate-limited?); retry later')
            pads = []
            for shape in package['dataStr']['shape']:
                if shape.startswith('PAD~'):
                    f = shape.split('~')  # PAD~shape~x~y~w~h~layer~net~number~..., units of 10 mil
                    pads.append([f[8], round(float(f[2]) * 0.254, 4), round(float(f[3]) * 0.254, 4)])
            self.data[lcsc] = {'footprint': package['title'], 'pads': sorted(pads)}
            CACHE.write_text(json.dumps(self.data, indent=1, sort_keys=True) + '\n')
        return self.data[lcsc]


def library_pads(fp, project):
    """Pads of the footprint as drawn in its library (unrotated, unflipped): [(number, x, y)]."""
    lib, name = str(fp.GetFPID().GetLibNickname()), str(fp.GetFPID().GetLibItemName())
    local = project / 'libraries' / f'{lib}.pretty'
    module = pcbnew.FootprintLoad(str(local if local.exists() else STOCK / f'{lib}.pretty'), name)
    return [(p.GetNumber(), pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)) for p in module.Pads()]


def _by_number(pads):
    groups = defaultdict(list)
    for n, x, y in pads:
        if n:
            groups[n].append((x, y))
    return {n: _centroid(v) for n, v in groups.items()}


def _centred(points, centre):
    return [(p[0] - centre[0], p[1] - centre[1]) for p in points]


def _number_errors(ours, theirs):
    keys = sorted(set(ours) & set(theirs))
    a, b = [ours[k] for k in keys], [theirs[k] for k in keys]
    a, b = _centred(a, _centroid(a)), _centred(b, _centroid(b))
    return {phi: max(math.dist(_rotate(p, phi), q) for p, q in zip(a, b)) for phi in (0, 90, 180, 270)}


def _pattern_errors(ours, theirs):
    a, b = _centred(ours, _centroid(ours)), _centred(theirs, _centroid(theirs))
    out = {}
    for phi in (0, 90, 180, 270):
        r = [_rotate(p, phi) for p in a]
        out[phi] = max(max(min(math.dist(p, q) for q in b) for p in r), max(min(math.dist(p, q) for p in r) for q in b))
    return out


def _solve(src, dst, name):
    """Angle a (0/90/180/270) with dst == rot(src, a), and how it was matched.

    Matched by pin number when the two pad sets share numbering, otherwise by land pattern, with
    pin 1 breaking the tie when the pattern is symmetric (a 2xN socket, a 4-pad switch).
    """
    s, d = _by_number(src), _by_number(dst)
    if len(set(s) & set(d)) >= 3:
        err = _number_errors(s, d)
        best = min(err, key=err.get)
        if err[best] < TOLERANCE_MM and sorted(err.values())[1] > err[best] + 0.5:
            return best, 'pin numbers'
    s_pts, d_pts = [(x, y) for _, x, y in src], [(x, y) for _, x, y in dst]
    err = _pattern_errors(s_pts, d_pts)
    fits = sorted(a for a, e in err.items() if e < TOLERANCE_MM)
    if not fits:
        raise SystemExit(f'{name} does not match the placed pads at any 90-degree rotation; '
                         'check the LCSC number or footprint')
    if len(fits) == 1:
        return fits[0], 'land pattern (pin numbers differ)'
    # Symmetric pattern: keep the angle that lands the reference's pin-1 pad(s) on our pin-1 pad(s).
    sc, dc = _centroid(s_pts), _centroid(d_pts)
    s1 = _centred([(x, y) for n, x, y in src if n == '1'], sc)
    d1 = _centred([(x, y) for n, x, y in dst if n == '1'], dc)
    if s1 and d1:
        pin1 = [a for a in fits if all(min(math.dist(_rotate(p, a), q) for q in d1) < TOLERANCE_MM for p in s1)]
        if len(pin1) == 1:
            return pin1[0], f'land pattern (symmetric at {fits}) + pin 1'
    return fits[0], f'land pattern symmetric at {fits}; pin 1 unresolved, check it in JLC preview'


def placement_rotation(fp, reference, name):
    """CPL rotation that puts the reference footprint (JLC's pads) onto this part's placed pads.

    Solved on the board's absolute pad positions, so it does not depend on how KiCad reports a
    flipped footprint's orientation. Bottom parts are compared as seen from the bottom (x mirrored).
    """
    placed = [(p.GetNumber(), pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)) for p in fp.Pads()]
    if fp.IsFlipped():
        placed = [(n, -x, y) for n, x, y in placed]
    angle, method = _solve(reference, placed, name)
    return (angle - 360 if angle > 180 else angle), method
