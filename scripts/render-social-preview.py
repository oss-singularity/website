#!/usr/bin/env python3
"""Render the social SVG with librsvg; --check verifies the existing PNG.

Requires rsvg-convert (librsvg2-bin), fontconfig and fonts-dejavu-core.
The reference image uses librsvg 2.58.0 and DejaVu Sans 2.37. Every run
renders twice and compares exact bytes before writing or checking the PNG.
No browser, network access, external artwork or Python package is required.
"""

import argparse
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "site/assets/social/oss-singularity-social-preview.svg"
OUTPUT = SOURCE.with_suffix(".png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the PNG needs rendering")
    args = parser.parse_args()
    renderer = shutil.which("rsvg-convert")
    fontconfig = shutil.which("fc-match")
    if not renderer or not fontconfig:
        raise SystemExit("Install librsvg2-bin, fontconfig and fonts-dejavu-core.")
    for face in ("DejaVu Sans", "DejaVu Sans:style=Bold"):
        family = subprocess.check_output([fontconfig, "-f", "%{family}", face], text=True)
        if family != "DejaVu Sans":
            raise SystemExit("DejaVu Sans is required; refusing a substitute font.")
    version = subprocess.check_output([renderer, "--version"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="oss-social-render-") as directory:
        renders = []
        for index in range(2):
            destination = Path(directory) / f"render-{index}.png"
            subprocess.run([
                renderer, "--width", "1200", "--height", "630",
                "--output", str(destination), str(SOURCE),
            ], check=True)
            renders.append(destination.read_bytes())
    image = renders[0]
    if image != renders[1]:
        raise SystemExit("Rendering was not deterministic; the existing PNG was preserved.")
    if image[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", image[16:24]) != (1200, 630):
        raise SystemExit("The renderer did not produce the required 1200 x 630 PNG.")
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != image:
            raise SystemExit("Social preview is stale. Run python3 scripts/render-social-preview.py.")
    else:
        OUTPUT.write_bytes(image)
    action = "Verified" if args.check else "Rendered"
    print(f"{action} 1200x630; two identical renders; SHA-256 {hashlib.sha256(image).hexdigest()}; {version}")


if __name__ == "__main__":
    main()
