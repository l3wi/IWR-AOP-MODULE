# Manufacturing (JLCPCB PCB + assembly)

`jlcpcb/` is generated from the saved board and schematic by `build_package.py`. Rebuild it after any design change:

```sh
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 build_package.py
```

The script refuses to run if the board's zone fills are stale (in KiCad: press B, save, rebuild).

| File | Upload / use |
|---|---|
| `IWR-AOP-MODULE-Gerbers.zip` | PCB order: six copper layers, mask, paste, silk, outline, PTH/NPTH drill, job file |
| `BOM.csv` | Assembly BOM, one line per JLC part number (JLC sample format, see `../references/`) |
| `CPL.csv` | Placement file, both sides; rotations derived per part from JLC's own footprint (below) |
| `bom-source.csv` | Full KiCad BOM with MPN and manufacturer (reference, don't upload) |
| `erc.json`, `drc.json`, `summary.json` | Checks and counts from the build; `summary.json` lists each multi-pin part's JLC rotation and how it was matched |

## Placement rotations

JLC places its own (LCSC/EasyEDA) footprint for each part, and those footprints have no common zero orientation. `jlc_rotation.py` fetches JLC's pads for every part with three or more pads (cached by LCSC number in `jlc-footprints.json`) and fits them directly onto the part's placed pads on the board. It matches by pin number, or by land pattern plus pin 1 where the libraries number pins differently. Bottom parts are fitted as seen looking at the bottom face, which is how JLC applies bottom rotations. The rotation that makes JLC's pin 1 land on ours is the CPL value, so JLC's package dot lands on the silkscreen pin-1 mark.

- A new LCSC part is fetched automatically on the next build. If its pads can't be matched, the build stops instead of guessing.
- Two-terminal parts are fitted against our own footprint; they place identically either way round.
- In JLC's preview, check pin 1 by the **package dot** rather than the pads. SOT-583, QFN-24, SOIC-8 and the LP87524 pads look aligned at 180° too.

## PCB order settings

| Setting | Value |
|---|---|
| Layers | 6 |
| Size | 85.6 × 33.7 mm |
| Thickness / material | 1.6 mm FR-4 |
| Stackup | Any JLC 6-layer 1.6 mm stack; prefer one with L3 (In2) closer to L2 (In1, GND) than to L4. No impedance control needed |
| Surface finish | ENIG (0.8 mm-pitch BGA) |
| Via covering | **Epoxy filled and copper capped (POFV)**. Not ink-plugged |
| Vias | 195 × 0.45/0.30 mm, 6 × 0.60/0.30 mm, 2 × 0.40/0.25 mm |
| Minimum track / space | 0.15 mm general; 0.09 mm inside the BGA escape |
| Holes | NPTH: 3 × 3.2 mm (M3), 2 × 0.65 mm (USB-C locating pegs). Plated slots: USB-C shell |

## Assembly

- Standard PCBA, **both sides**: 12 placements on the front, 129 on the back, 39 BOM lines. Test pads, mounting holes and `TH300` are board-only. `C605` is do-not-populate (reset fallback, spec §3.3); the builder leaves it out of the BOM and CPL.
- Check every part in JLC's placement preview, especially `U300` (BGA), `U600`, `U117`, `U200`, `U301`, `D200`/`D201`, `J200`, `J301` and `J400`/`J401`. `JP300` is a PCB-only solder jumper (no part).
- Some extended capacitors and resistors are deliberate: the PMIC input/output capacitors (TI requires X7R/X7T; no basic part at those values), the 390 pF / 3.9 Ω PMIC snubbers, and the 47 nF `VBGAP` capacitor.
- Stock to watch (5 October 2026): `U301` MX25L3233F (C190802) 3 in stock, which caps an order at 3 boards; `U600` LP87524J (C701982) 172.

Component prices: [`../docs/bom-cost.csv`](../docs/bom-cost.csv). Actual order cost: [README](../README.md#cost).
