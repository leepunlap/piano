#!/usr/bin/env python3
"""Mask a scanned score page down to its melody staves.

Why
---
A score page is not a melody.  Beside the music sit chord symbols, guitar
chord diagrams, lyrics, fingering numbers and rehearsal marks, and every one of
them is a trap for an optical music reader: a guitar diagram is a little grid
of lines that looks exactly like a staff, a fingering number is a small blob
that looks exactly like a notehead.

Audiveris reads a page like this badly — it finds the staves and then reads
the wrong notes.  But it is very good at *one* staff of clean notation.  So
the page is masked down to the melody staves, and the reader is left with an
easy problem.

How
---
The staff geometry comes from a first pass: Audiveris's grid step finds every
staff and models every line as a polyline, which is how the slight warp of a
photographed page is tracked.  That geometry then says exactly which pixels
belong to the melody and which belong to something else.

Two things are removed:

  * everything outside the melody staff's band, which takes the other staves,
    the lyrics, the chord symbols and the guitar diagrams with it;
  * inside the band, anything above the top staff line that is not shaped
    like a notehead — that is where the chord symbols and the fingerings sit,
    and they are exactly the size and roundness of a notehead.

What is kept inside the band is the staff lines, the notes, the stems, the
beams, the flags, the rests, the barlines, the clef and the key and time
signatures.  The time signature matters more than it looks: without it the
reader cannot work out how long a bar is, and every bar comes out the wrong
length.

Usage:
    omr_mask.py <page.omr> <source.png> <out.png> [--overlay overlay.png]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from omr_bands import StaffGeom, read_omr

try:
    from scipy import ndimage
except ImportError:  # pragma: no cover
    ndimage = None


def sauvola(gray: np.ndarray, window: int = 31, k: float = 0.2) -> np.ndarray:
    """Local thresholding: the printed pages are grey paper with black ink."""
    if ndimage is None:
        return gray < gray.mean()
    g = gray.astype(np.float64)
    mean = ndimage.uniform_filter(g, size=window)
    mean_sq = ndimage.uniform_filter(g * g, size=window)
    std = np.sqrt(np.clip(mean_sq - mean * mean, 0.0, None))
    return g <= mean * (1.0 + k * (std / 128.0 - 1.0))


def notehead_like(labels: np.ndarray, ink: np.ndarray, min_px: int = 6) -> np.ndarray:
    """Which connected components have the size and shape of a notehead.

    A notehead is a small, solid, wide-ish blob.  A fingering number is small
    but not solid — it is a ring or a stroke — and a chord symbol is not small
    at all.  Filling the shape and measuring its aspect separates them.
    """
    out = np.zeros(len(labels), dtype=bool)
    objects = ndimage.find_objects(labels)
    for i, sl in enumerate(objects):
        if sl is None:
            continue
        block = labels[sl] == (i + 1)
        area = int(block.sum())
        if area < min_px:
            continue
        ys, xs = np.nonzero(block)
        h = ys.max() - ys.min() + 1
        w = xs.max() - xs.min() + 1
        fill = area / float(h * w)
        aspect = w / float(h)
        if fill >= 0.55 and 0.55 <= aspect <= 2.4:
            out[i] = True
    return out


def build_mask(
    img: Image.Image,
    staves: list[StaffGeom],
    systems: dict[int, list[int]],
    above: float = 2.6,
    below: float = 2.9,
    binary: bool = True,
) -> tuple[Image.Image, np.ndarray]:
    """Return (clean score image, keep mask).

    The input is expected to be a binary rendering of the page — black ink on
    white paper — which is what a good binarisation of a printed score gives.
    On such an image the staff lines are already clean and crisp, so they are
    kept rather than redrawn: redrawing them risks making them thicker than a
    reader expects, and thicker lines make every other measurement wrong.

    The keep mask says which original pixels survived: everything outside the
    melody staff's band is gone, and above the top staff line only things
    shaped like a notehead are kept, because that is where the chord symbols
    and fingering numbers live and they are the size of a notehead.
    """
    gray = np.asarray(img.convert("L"))
    ink = (gray < 128) if binary else sauvola(gray)
    h, w = ink.shape
    keep = np.zeros((h, w), dtype=bool)
    by_id = {s.sid: s for s in staves}
    melody_staves: list[StaffGeom] = []
    prev_bottom = 0

    for sys_id, ids in sorted(systems.items()):
        ids = [i for i in ids if i in by_id]
        if not ids:
            continue
        # The melody is the top staff of the system.
        top = min(ids, key=lambda i: by_id[i].line_y(0, by_id[i].left))
        staff = by_id[top]
        melody_staves.append(staff)
        inter = staff.interline

        x0 = max(0, int(staff.left) - 10)
        x1 = min(w, int(staff.right) + 10)
        top_y = staff.line_y(0, staff.left)
        bot_y = staff.line_y(4, staff.left)
        y0 = max(0, int(top_y - above * inter))
        y1 = min(h, int(bot_y + below * inter))

        band = np.zeros((h, w), dtype=bool)
        band[y0:y1, x0:x1] = True
        keep |= band

        # Above the top staff line, keep only what is music.  Two things live
        # there and only one of them belongs: the chord symbols and the
        # fingering numbers are clutter, but a high note — or a beamed group
        # that reaches up — is notation and must stay.
        #
        # The test is whether the clutter lies wholly above the staff.  A
        # beamed group is a single connected blob of noteheads, stems and beam,
        # so it straddles the staff and is kept; a chord symbol does not touch
        # the staff at all and goes.  Removing a component outright would take
        # the noteheads of a beamed group with it, because they are all one
        # piece, so only the part that is genuinely above the staff is cut.
        above_mask = np.zeros((h, w), dtype=bool)
        header_end = int(staff.header_stop) if staff.header_stop else x0
        above_mask[prev_bottom : int(top_y), max(x0, header_end) : x1] = True
        region = ink & above_mask
        labels, n = ndimage.label(region)
        if n:
            keep_labels = notehead_like(labels, region)
            objects = ndimage.find_objects(labels)
            for i in range(n):
                if keep_labels[i]:
                    continue
                sl = objects[i]
                if sl is None:
                    continue
                bottom = sl[0].stop - 1
                if bottom >= top_y + 0.10 * inter:
                    continue  # it reaches into the staff: it is notation
                keep &= ~(labels == (i + 1))
        prev_bottom = y1

    arr = np.full((h, w), 255, dtype=np.uint8)
    arr[ink & keep] = 0
    return Image.fromarray(arr), keep


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    omr_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    overlay_path = None
    if "--overlay" in sys.argv:
        overlay_path = Path(sys.argv[sys.argv.index("--overlay") + 1])
    source = None
    if len(sys.argv) > 3 and sys.argv[3] != "--overlay":
        source = Path(sys.argv[3])

    staves, systems = read_omr(omr_path)

    # The binarised rendering is the right source: it is the page with the
    # staff lines already resolved, which a photograph never quite gives.
    if source is None:
        import io
        import zipfile

        with zipfile.ZipFile(omr_path) as z:
            name = next(n for n in z.namelist() if n.endswith("BINARY.png"))
            img = Image.open(io.BytesIO(z.read(name)))
    else:
        img = Image.open(source)

    masked, keep = build_mask(img, staves, systems)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    masked.save(out_path)

    if overlay_path is not None:
        rgb = np.asarray(img.convert("RGB")).copy()
        rgb[~keep] = (255, 255, 255)
        rgb[keep & (np.asarray(img.convert("L")) < 128)] = (200, 30, 30)
        Image.fromarray(rgb).save(overlay_path)

    print(
        f"{omr_path.name}: {len(staves)} staves, {len(systems)} systems, "
        f"kept {keep.mean() * 100:.1f}% of the page -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
