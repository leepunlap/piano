#!/usr/bin/env python3
"""Show what was read, against what is printed.

The reading of a score is only worth as much as the evidence for it, so every
system is shown twice: the printed page on top, and the same page with the
recognition drawn over it underneath.  Where the red marks sit on the black
music, the reading is right; where they do not, it is not.

Usage:
    compare.py <page.omr> <masked.png> [--out out.png] [--system N]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from omr_bands import read_omr
from read_page import read_page


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    omr_path = Path(sys.argv[1])
    masked_path = Path(sys.argv[2])
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(".")
    only = None
    if "--system" in sys.argv:
        only = int(sys.argv[sys.argv.index("--system") + 1])

    staves, systems = read_omr(omr_path)
    img = Image.open(masked_path).convert("RGB")
    gray = np.asarray(img.convert("L"))
    ink = gray < 128

    read = read_page(ink, staves, systems, clef="treble")

    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in read:
        if only is not None and entry["system"] != only:
            continue
        staff = entry["staff"]
        inter = staff.interline
        x0 = int(staff.left)
        x1 = int(staff.right)
        top = int(staff.line_y(0, staff.left) - inter * 3.2)
        bottom = int(staff.line_y(4, staff.right) + inter * 3.2)
        top = max(0, top)
        bottom = min(img.height, bottom)

        strip = img.crop((x0, top, x1, bottom))
        overlay = strip.copy()
        draw = ImageDraw.Draw(overlay)
        for note in entry["notes"]:
            colour = (220, 30, 30)
            w = inter * 0.67
            h = inter * 0.5
            draw.ellipse(
                [note.x - x0 - w, note.y - top - h, note.x - x0 + w, note.y - top + h],
                outline=colour,
                width=max(1, int(inter * 0.14)),
            )
            if note.stem:
                draw.line(
                    [
                        (note.x - x0, note.y - top),
                        (note.x - x0, note.y - top + note.stem * inter * 3.0),
                    ],
                    fill=colour,
                    width=max(1, int(inter * 0.12)),
                )
            label = note.duration[0].upper() + ("." if note.dotted else "")
            draw.text((note.x - x0 - 3, note.y - top - inter * 1.1), label, fill=colour)
            if note.rest:
                draw.text((note.x - x0 - 3, note.y - top - inter * 1.1), "R", fill=colour)

        canvas = Image.new("RGB", (strip.width, strip.height * 2 + 6), (255, 255, 255))
        canvas.paste(strip, (0, 0))
        canvas.paste(overlay, (0, strip.height + 6))
        path = out_dir / f"sys{entry['system']:02d}.png"
        canvas.save(path)
        print(f"{path}  ({len(entry['notes'])} notes, {len(entry['barlines'])} barlines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
