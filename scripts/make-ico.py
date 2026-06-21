#!/usr/bin/env python3
"""Render the SVG app icon into a multi-resolution Windows ``.ico``.

The Windows build (PyInstaller + Inno Setup) needs a ``.ico`` for the executable,
Start-Menu shortcut and Add/Remove-Programs entry. Windows itself has no icon
theme, so unlike Linux we cannot rely on ``QIcon.fromTheme`` — the icon has to be
baked into the binary. We generate it here on a machine that *does* have an SVG
rasteriser and commit the result, so the Windows CI runner needs no SVG tooling.

Run from the repo root (rebuild whenever the SVG changes):

    .venv/bin/python scripts/make-ico.py

Uses ``rsvg-convert`` or ``inkscape`` — whichever is installed — to render the
source SVG at several sizes, then packs the PNGs into an ICO by hand. Vista and
later accept PNG-compressed icon entries, so no extra image library is needed.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_SVG = ROOT / "data" / "icons" / "io.github.theyoungrossco.netgrip.svg"
OUT_ICO = ROOT / "data" / "icons" / "netgrip.ico"

# 256 is the largest the ICO directory can describe (a 0 byte means 256); the
# smaller sizes keep shortcuts crisp at every shell zoom level.
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _render_png(svg: Path, size: int, dest: Path) -> None:
    """Rasterise *svg* to a square *size* PNG at *dest* with whatever is around."""
    if shutil.which("rsvg-convert"):
        cmd = ["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(dest), str(svg)]
    elif shutil.which("inkscape"):
        cmd = [
            "inkscape", str(svg),
            "--export-type=png", f"--export-filename={dest}",
            "-w", str(size), "-h", str(size),
        ]
    else:
        sys.exit("error: need rsvg-convert or inkscape to rasterise the SVG")
    subprocess.run(cmd, check=True, capture_output=True)


def _pack_ico(pngs: list[tuple[int, bytes]], dest: Path) -> None:
    """Write an ICO file embedding each (size, png-bytes) entry verbatim."""
    count = len(pngs)
    # ICONDIR header, then one 16-byte ICONDIRENTRY per image, then the blobs.
    offset = 6 + 16 * count
    header = struct.pack("<HHH", 0, 1, count)  # reserved, type=icon, image count
    entries = bytearray()
    blobs = bytearray()
    for size, data in pngs:
        dim = 0 if size >= 256 else size  # 0 encodes 256 in a single byte
        entries += struct.pack(
            "<BBBBHHII",
            dim, dim, 0, 0,        # width, height, palette size, reserved
            1, 32,                 # colour planes, bits per pixel
            len(data), offset,     # bytes in resource, offset to it
        )
        blobs += data
        offset += len(data)
    dest.write_bytes(header + bytes(entries) + bytes(blobs))


def main() -> int:
    if not SRC_SVG.exists():
        sys.exit(f"error: source SVG not found: {SRC_SVG}")
    pngs: list[tuple[int, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="netgrip-ico-") as tmp:
        for size in SIZES:
            png = Path(tmp) / f"{size}.png"
            _render_png(SRC_SVG, size, png)
            pngs.append((size, png.read_bytes()))
    _pack_ico(pngs, OUT_ICO)
    print(f"wrote {OUT_ICO} ({len(SIZES)} sizes, {OUT_ICO.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
