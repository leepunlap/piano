#!/usr/bin/env python3
"""Read the chord symbols off the page.

The chords are printed above the staff, and they are the one part of the
score that has to be read as *text* rather than as notation.  They are also
the part that must not be lost: the practice system may change the tempo and
the style of what it plays, but it must keep the harmony.

So the region above the melody staff is taken, the pieces of text in it are
grouped into symbols, and each symbol is read on its own.

Usage:
    ocr_chords.py <page.omr> <binary.png> [--out chords.json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from omr_bands import read_omr

try:
    from scipy import ndimage
except ImportError:  # pragma: no cover
    ndimage = None

TESSDATA = Path(__file__).parent / "tessdata"

CHORD_PATTERN = re.compile(
    r"^[A-G][#b]?(m|maj|dim|aug|sus[24]|[0-9]|add9|M)?([#b]?[0-9]*)?(/[A-G][#b]?)?$"
)


def read_text(crop: Image.Image) -> str:
    """Read one symbol with tesseract.

    Some of the scores print their chord symbols inside a little box, and the
    box is a straight line around the text that confuses the reader.  So the
    symbol is read twice: once as it stands, and once with the long straight
    runs — the edges of the box — taken out.  Whichever reads better is the
    answer.
    """
    plain = _ocr(crop)
    if plain and _looks_like_chord(plain):
        return plain
    stripped = _ocr(_strip_box(crop))
    return stripped or plain


def _looks_like_chord(text: str) -> bool:
    return bool(CHORD_PATTERN.match(text.strip().replace(" ", "")))


def _strip_box(crop: Image.Image) -> Image.Image:
    """Take the long straight runs out of a crop."""
    ink = np.asarray(crop.convert("L")) < 128
    if not ink.any():
        return crop
    h, w = ink.shape
    # A box edge runs the whole way across; a letter does not.
    for y in np.flatnonzero(ink.sum(axis=1) > w * 0.8):
        ink[y] = False
    for x in np.flatnonzero(ink.sum(axis=0) > h * 0.8):
        ink[:, x] = False
    return Image.fromarray(np.where(ink, 0, 255).astype(np.uint8))


def _ocr(crop: Image.Image) -> str:
    """Read a crop with tesseract."""
    # Tesseract wants a decent size, some white around the ink, and no
    # confusion about there being only one line of text on the page.
    scale = 3
    big = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    canvas = Image.new("L", (big.width + 24, big.height + 24), 255)
    canvas.paste(big, (12, 12))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        canvas.save(tmp.name)
        path = tmp.name
    try:
        result = subprocess.run(
            [
                "tesseract",
                path,
                "stdout",
                "--tessdata-dir",
                str(TESSDATA),
                "-l",
                "eng",
                "--psm",
                "8",
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    return result.stdout.strip()


def group_symbols(ink: np.ndarray, min_gap: int) -> list[tuple[int, int, int]]:
    """Group the pieces of text above the staff into symbols.

    A symbol such as "Em/D#" is several separate pieces of ink, so the pieces
    are grouped by how close they are to one another.
    """
    labels, n = ndimage.label(ink)
    boxes = []
    for i, sl in enumerate(ndimage.find_objects(labels)):
        if sl is None:
            continue
        ys, xs = np.nonzero(labels[sl] == (i + 1))
        if len(xs) < 18:
            continue
        if ys.max() - ys.min() < 8 or xs.max() - xs.min() < 2:
            continue  # speckle, not a letter
        # The offsets of the slice have to be added back on: the coordinates
        # inside a slice are measured from the slice's own corner.
        boxes.append(
            (
                int(xs.min()) + sl[1].start,
                int(xs.max()) + sl[1].start,
                int(ys.min()) + sl[0].start,
                int(ys.max()) + sl[0].start,
            )
        )
    boxes.sort()
    groups: list[list[tuple[int, int, int, int]]] = []
    for box in boxes:
        if groups and box[0] - groups[-1][-1][1] <= min_gap:
            groups[-1].append(box)
        else:
            groups.append([box])
    out = []
    for group in groups:
        x0 = min(b[0] for b in group)
        x1 = max(b[1] for b in group)
        y0 = min(b[2] for b in group)
        y1 = max(b[3] for b in group)
        out.append((x0, x1, y0, y1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("omr", type=Path)
    ap.add_argument("binary", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    staves, systems = read_omr(args.omr)
    img = Image.open(args.binary).convert("L")
    ink = np.asarray(img) < 128
    by_id = {s.sid: s for s in staves}

    found = []
    prev_bottom = 0
    for sys_id, ids in sorted(systems.items()):
        ids = [i for i in ids if i in by_id]
        if not ids:
            continue
        top_id = min(ids, key=lambda i: by_id[i].line_y(0, by_id[i].left))
        staff = by_id[top_id]
        inter = staff.interline
        top_y = staff.line_y(0, staff.left)
        x0 = int(staff.left)
        x1 = int(staff.right)
        header_end = int(staff.header_stop) if staff.header_stop else x0

        region = np.zeros_like(ink)
        region[max(0, prev_bottom) : int(top_y), header_end:x1] = True
        region &= ink

        boxes = group_symbols(region, int(inter * 0.55))
        for bx0, bx1, by0, by1 in boxes:
            if by1 - by0 > inter * 2.2:
                continue
            crop = img.crop((bx0 - 3, by0 - 3, bx1 + 4, by1 + 4))
            text = read_text(crop)
            text = text.strip().replace(" ", "")
            if not text or not CHORD_PATTERN.match(text):
                continue
            found.append(
                {
                    "page": args.omr.stem,
                    "system": sys_id,
                    "x": (bx0 + bx1) / 2.0,
                    "chord": text,
                }
            )
        prev_bottom = int(top_y)

    print(f"{args.omr.name}: {len(found)} chord symbols")
    for item in found[:12]:
        print(f"   {item['chord']:8s} x={item['x']:.0f}")

    if args.out:
        args.out.write_text(json.dumps(found, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
