"""Stitch orbit-mode frames (build_product_scene.py --mode orbit) into one MP4.

Usage: python3 tools/render/stitch_orbit.py ORBIT_DIR OUT.mp4

Reads ORBIT_DIR/shots.txt for fps and shot order and joins the shots with hard cuts.
Relative paths resolve under build/product-renders/, independent of the working directory.
"""
import subprocess
import sys
from pathlib import Path

RENDER_ROOT = Path(__file__).resolve().parents[2] / "build" / "product-renders"


def main():
    src, out = (RENDER_ROOT / sys.argv[1]).resolve(), (RENDER_ROOT / sys.argv[2]).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = (src / "shots.txt").read_text().split("\n")
    fps = int(lines[0].split()[1])
    shots = [(name, int(n)) for name, n in (ln.split() for ln in lines[1:] if ln.strip())]

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for name, _ in shots:
        cmd += ["-framerate", str(fps), "-i", str(src / "frames" / name / "%04d.png")]
    inputs = "".join(f"[{k}:v]" for k in range(len(shots)))
    graph = f"{inputs}concat=n={len(shots)}:v=1:a=0,format=yuv420p[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-r", str(fps),
            "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    total = sum(n for _, n in shots) / fps
    print(f"wrote {out} ({total:.2f} s, {len(shots)} shots)")


if __name__ == "__main__":
    main()
