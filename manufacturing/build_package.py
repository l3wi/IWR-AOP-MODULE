#!/usr/bin/env python3
"""Build the JLCPCB PCB + PCBA package for IWR-AOP-MODULE from the saved board and schematic.

Writes manufacturing/jlcpcb/: gerbers + drill (zipped), BOM.csv, CPL.csv, ERC/DRC reports, summary.json.
Run with KiCad's Python:
  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 build_package.py
"""
import csv
from collections import Counter
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pcbnew

from jlc_rotation import JlcFootprints, library_pads, placement_rotation

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'jlcpcb'
PROJECT = ROOT.parent
BOARD = PROJECT / 'IWR-AOP-MODULE.kicad_pcb'
SCH = PROJECT / 'IWR-AOP-MODULE.kicad_sch'
CLI = '/opt/homebrew/bin/kicad-cli'
NAME = 'IWR-AOP-MODULE'
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'B.Cu', 'F.Paste', 'B.Paste',
          'F.Silkscreen', 'B.Silkscreen', 'F.Mask', 'B.Mask', 'Edge.Cuts']


def run(*args):
    subprocess.run([CLI, *args], check=True, stdout=subprocess.DEVNULL)


# Gerbers plot the zone fills saved in the board; a stale fill can leave plane copper over a
# newly placed via or pad. Refuse to export unless a fresh refill reproduces the saved fills.
def fill_areas(b):
    return [round(z.GetFilledPolysList(l).Area() / 1e12, 3) for z in b.Zones() for l in z.GetLayerSet().Seq()]
check = pcbnew.LoadBoard(str(BOARD))
saved_fill = fill_areas(check)
pcbnew.ZONE_FILLER(check).Fill(check.Zones())
if fill_areas(check) != saved_fill:
    raise SystemExit('Zone fills are stale: in KiCad press B (Fill All Zones), save, then rebuild.')

OUT.mkdir(exist_ok=True)
ger = Path(tempfile.mkdtemp())  # plotted here, shipped only inside the zip
run('pcb', 'export', 'gerbers', '--output', str(ger) + '/', '--layers', ','.join(LAYERS), '--precision', '6',
    '--subtract-soldermask', str(BOARD))
run('pcb', 'export', 'drill', '--output', str(ger) + '/', '--format', 'excellon', '--drill-origin', 'absolute',
    '--excellon-units', 'mm', '--excellon-zeros-format', 'decimal', '--excellon-oval-format', 'alternate',
    '--excellon-separate-th', str(BOARD))

# Release checks, saved with the package
subprocess.run([CLI, 'sch', 'erc', '--format', 'json', '--severity-all', '--output', str(OUT / 'erc.json'), str(SCH)],
               check=True, stdout=subprocess.DEVNULL)
subprocess.run([CLI, 'pcb', 'drc', '--format', 'json', '--schematic-parity', '--severity-all',
                '--output', str(OUT / 'drc.json'), str(BOARD)], check=True, stdout=subprocess.DEVNULL)
drc = json.loads((OUT / 'drc.json').read_text())
erc = json.loads((OUT / 'erc.json').read_text())

# BOM (schematic is the source of sourcing fields). Do-not-populate parts (e.g. C605) stay out of BOM and CPL.
run('sch', 'export', 'bom', '--preset', 'JLCPCB', '--format-preset', 'CSV', '--exclude-dnp',
    '--output', str(OUT / 'bom-source.csv'), str(SCH))
bom = []
for row in csv.DictReader((OUT / 'bom-source.csv').open()):
    comment = [row.get('Comment', '').strip()]
    for key in ('Rating', 'Tolerance'):
        value = (row.get(key) or '').strip()
        if value and value.lower() not in comment[0].lower():
            comment.append(value)
    bom.append({'Comment': ' '.join(v for v in comment if v), 'Designator': row['Designator'].strip(),
                'Footprint': row['Footprint'].split(':')[-1], 'LCSC Part #': row['LCSC'].strip(),
                'Quantity': row['Qty'].strip(), 'MPN': row['MPN'].strip(), 'Manufacturer': row['Manufacturer'].strip()})

# JLC wants exactly one BOM line per JLCPCB/LCSC part number (see
# references/JLCPCB_Sample-BOM_JLCSMT.xlsx). KiCad groups by value/footprint/fields,
# so one part can arrive split across lines (BOOT vs RESET switch, "100nF" vs "100nF 16V").
# Merge on the part number; parts without one stay grouped by comment + footprint.
groups = {}
for r in bom:
    key = r['LCSC Part #'] or (r['Comment'], r['Footprint'])
    groups.setdefault(key, []).append(r)
merged_lines = {}
bom = []
for key, rows in groups.items():
    for field in ('Footprint', 'MPN', 'Manufacturer'):
        values = {r[field] for r in rows}
        if len(values) > 1:
            raise SystemExit(f'{key}: conflicting {field} across BOM lines: {sorted(values)}')
    comments = list(dict.fromkeys(r['Comment'] for r in rows))
    refs = [d.strip() for r in rows for d in r['Designator'].split(',') if d.strip()]
    if len(rows) > 1:
        merged_lines[rows[0]['LCSC Part #']] = comments
    bom.append({**rows[0], 'Comment': comments[0] if len(comments) == 1 else (rows[0]['MPN'] or ' / '.join(comments)),
                'Designator': ','.join(refs), 'Quantity': str(len(refs))})
unsourced = [r['Designator'] for r in bom if not r['LCSC Part #']]
bom_refs = {d.strip() for r in bom for d in r['Designator'].split(',')}

# CPL: pad-field centre, JLC Y sign, rotation from JLC's own footprint for each part (jlc_rotation.py)
board = pcbnew.LoadBoard(str(BOARD))
lcsc_by_ref = {d.strip(): r['LCSC Part #'] for r in bom for d in r['Designator'].split(',')}
jlc = JlcFootprints()
cpl, offsets = [], {}
for fp in board.GetFootprints():
    ref = fp.GetReference()
    pads = list(fp.Pads())
    if ref not in bom_refs or not pads:
        continue
    xs = [pcbnew.ToMM(p.GetPosition().x) for p in pads]
    ys = [pcbnew.ToMM(p.GetPosition().y) for p in pads]
    x, y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    ours = library_pads(fp, PROJECT)
    bottom = fp.IsFlipped()
    if len(ours) <= 2:  # two-terminal: JLC's footprint is assumed to match ours (pad 1 left)
        angle, method = placement_rotation(fp, ours, f'{ref} library footprint')
    else:
        part = jlc.get(lcsc_by_ref[ref])
        angle, method = placement_rotation(fp, part['pads'], f"{ref}: JLC footprint {part['footprint']}")
        offsets[ref] = {'lcsc': lcsc_by_ref[ref], 'jlc_footprint': part['footprint'], 'matched_by': method,
                        'side': 'B' if bottom else 'T', 'cpl_rotation_deg': angle}
    cpl.append([ref, f'{x:.6f}', f'{-y:.6f}', 'B' if bottom else 'T', f'{angle:.6f}'])

# JLC rejects BOM designators with no placement; drop them from the uploaded BOM and report them.
missing_cpl = sorted(bom_refs - {r[0] for r in cpl})
kept = []
for r in bom:
    refs = [d.strip() for d in r['Designator'].split(',') if d.strip() not in missing_cpl]
    if refs:
        kept.append({**r, 'Designator': ','.join(refs), 'Quantity': str(len(refs))})
bom = kept
# Columns and order follow JLC's sample BOM; MPN/Manufacturer stay in bom-source.csv.
with (OUT / 'BOM.csv').open('w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['Comment', 'Designator', 'Footprint', 'JLCPCB Part #'])
    w.writerows([r['Comment'], r['Designator'], r['Footprint'], r['LCSC Part #']] for r in bom)
with (OUT / 'CPL.csv').open('w', newline='') as f:
    w = csv.writer(f); w.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation']); w.writerows(sorted(cpl))

vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
bb = board.GetBoardEdgesBoundingBox()
summary = {
    'board_mm': [round(pcbnew.ToMM(bb.GetWidth()), 2), round(pcbnew.ToMM(bb.GetHeight()), 2)],
    'copper_layers': board.GetCopperLayerCount(),
    'thickness_mm': round(pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness()), 2),
    'vias': len(vias),
    'min_via_drill_mm': round(min(pcbnew.ToMM(v.GetDrill()) for v in vias), 3) if vias else None,
    'min_track_mm': round(min(pcbnew.ToMM(t.GetWidth()) for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T), 3),
    'bom_lines': len(bom), 'bom_parts': sum(int(r['Quantity']) for r in bom),
    'placements_top': sum(1 for r in cpl if r[3] == 'T'), 'placements_bottom': sum(1 for r in cpl if r[3] == 'B'),
    'unsourced_bom_lines': unsourced, 'dropped_from_bom_no_footprint': missing_cpl,
    'merged_by_part_number': merged_lines,
    'jlc_rotation_by_part': dict(sorted(offsets.items())),
    'erc_violations': sum(len(s['violations']) for s in erc['sheets']),
    'drc_violations_by_type': dict(Counter(f"{v['severity']}:{v['type']}" for v in drc['violations'])),
    'drc_unconnected': len(drc['unconnected_items']), 'drc_schematic_parity': len(drc['schematic_parity']),
}
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
with zipfile.ZipFile(OUT / f'{NAME}-Gerbers.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(ger.iterdir()):
        z.write(p, p.name)
shutil.rmtree(ger)
print(json.dumps(summary, indent=2))
