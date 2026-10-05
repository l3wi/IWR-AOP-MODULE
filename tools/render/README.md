# Product renders

Run these commands from the repository root. Requires KiCad 10, Blender (tested with 5.2.2 LTS), Python 3 with Pillow, and FFmpeg. Texture lettering uses macOS's `/System/Library/Fonts/HelveticaNeue.ttc`; on another platform, set `FONT` in `make_aop_texture.py` to an installed font and face index.

Check DRC before exporting; review the accepted findings in the [hardware specification](../../docs/STG-HW-SPEC-004_IWR-AOP-MODULE.md) §7.3. All generated assets belong under the ignored `build/product-renders/` directory. The scripts find the repository from their location: relative GLB paths resolve from the repository root, while relative render/stitch paths resolve from `build/product-renders/` (absolute paths remain absolute).

## Export and textures

```sh
mkdir -p build/product-renders
kicad-cli pcb export step --force --no-dnp --subst-models -o build/product-renders/IWR-AOP-MODULE.step IWR-AOP-MODULE.kicad_pcb
kicad-cli pcb export glb --force --no-dnp --subst-models --include-tracks --include-pads --include-zones --include-silkscreen --include-soldermask --cut-vias-in-body -o build/product-renders/IWR-AOP-MODULE.glb IWR-AOP-MODULE.kicad_pcb
python3 tools/render/make_aop_texture.py
```

The four 3000 × 3000 package maps are written to `build/product-renders/aop-top/`. They reconstruct antenna patches and markings from TI package imagery; they are render textures, not RF design data.

## Stills

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python tools/render/build_product_scene.py -- build/product-renders/IWR-AOP-MODULE.glb stills --views hero,top,sensor-detail,back --samples 256 --res 2400x1600
```

Writes four PNGs and `IWR-AOP-MODULE-product-renders.blend` under `build/product-renders/stills/`. Add `--dof deep` for deeper focus and `-deep-dof` filenames.

## Orbit video

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python tools/render/build_product_scene.py -- build/product-renders/IWR-AOP-MODULE.glb orbit-720p --mode orbit --res 1280x720 --samples 64
python3 tools/render/stitch_orbit.py orbit-720p orbit-720p/IWR-AOP-MODULE-orbit-720p.mp4
```

Renders three camera moves at 24 fps, start/end images under `orbit-720p/keyframes/`, numbered PNGs under `orbit-720p/frames/`, and `shots.txt` for stitching into an 8.5-second H.264 MP4 with hard cuts. Use `--keys-only 1` to render only the six endpoint images; it does not write a frame manifest.

## Small smoke renders

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python tools/render/build_product_scene.py -- build/product-renders/IWR-AOP-MODULE.glb publication-smoke/stills --views hero --samples 1 --res 160x90
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python tools/render/build_product_scene.py -- build/product-renders/IWR-AOP-MODULE.glb publication-smoke/orbit --mode orbit --keys-only 1 --key-res 160x90 --key-samples 1
```

Writes the hero still, Blender scene and six orbit keyframes under `build/product-renders/publication-smoke/`.

## Header GIF

The published GIF uses 784 × 441 pixels, 12 fps and a global 64-color palette. Ordered Bayer dithering at scale 4 balances gradient banding against visible grain and file size. GIF timing alternates 80/90 ms delays; `-loop 0` loops indefinitely.

```sh
ffmpeg -y -i build/product-renders/orbit-720p/IWR-AOP-MODULE-orbit-720p.mp4 -vf 'fps=12,scale=784:-1:flags=lanczos,palettegen=max_colors=64:stats_mode=full:reserve_transparent=0' -frames:v 1 -update 1 build/product-renders/orbit-palette.png
ffmpeg -y -i build/product-renders/orbit-720p/IWR-AOP-MODULE-orbit-720p.mp4 -i build/product-renders/orbit-palette.png -filter_complex '[0:v]fps=12,scale=784:-1:flags=lanczos[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle' -loop 0 build/product-renders/orbit.gif
```
