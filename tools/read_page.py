#!/usr/bin/env python3
"""Read a score page's melody and draw what was read back onto it.

The overlay is the check.  A recognition that cannot be seen to be right is a
recognition that cannot be trusted, so every note that is read is drawn back
on the staff: a head at its place and height, a stem, and a tick showing the
length of time it takes.  What the drawing shows against the printed music is
the verdict.

Usage:
    read_page.py <page.omr> <page.png> [--clef treble] [--overlay out.png]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from omr_bands import read_omr
from reader import (
    DURATION_BEATS,
    assign_durations,
    detect_heads,
    detect_stems,
    find_beams,
    find_barlines,
    remove_beams,
    remove_staff_lines,
)


def read_page(
    ink: np.ndarray,
    staves,
    systems,
    clef: str = "treble",
) -> list[list]:
    """Read every system of a page and return its notes."""
    by_id = {s.sid: s for s in staves}
    result = []
    for sys_id, ids in sorted(systems.items()):
        ids = [i for i in ids if i in by_id]
        if not ids:
            continue
        top = min(ids, key=lambda i: by_id[i].line_y(0, by_id[i].left))
        staff = by_id[top]
        inter = staff.interline
        # The noteheads are found on a page with the staff lines and the beams
        # taken out: a staff line and a beam both fill a notehead-sized
        # ellipse almost as well as a notehead does.
        bare = remove_staff_lines(ink, staff)
        beams = find_beams(bare, staff, inter)
        clean = remove_beams(ink, beams, inter)
        notes = detect_heads(clean, staff, clef=clef)
        detect_stems(ink, notes, staff, inter)
        # A note with no stem that sits above the staff is not music: it is a
        # fingering number or some other mark.  A real note above the staff
        # has a stem.
        top_y = staff.line_y(0, staff.left)
        # A note with no stem that sits above the staff is not music: it is a
        # fingering number or some other mark.  A real note above the staff
        # has a stem.
        notes = [n for n in notes if n.stem != 0 or n.y > top_y + 0.2 * inter]
        # A hollow head with no stem is a whole note, which is rare in this
        # music; far more often it is a scrap of beam that happened to look
        # like a ring.  A whole note has a stem or it does not happen here.
        notes = [n for n in notes if not (n.hollow and n.stem == 0)]
        assign_durations(notes, ink, staff, inter, beams)
        barlines = find_barlines(ink, staff, inter)
        result.append({"system": sys_id, "staff": staff, "notes": notes, "barlines": barlines})
    return result


def draw_overlay(img: Image.Image, systems_read) -> Image.Image:
    from reader import LETTERS

    out = img.convert("RGB")
    draw = ImageDraw.Draw(out)
    for entry in systems_read:
        staff = entry["staff"]
        inter = staff.interline
        for note in entry["notes"]:
            colour = (220, 30, 30)
            # A hollow ring where the head was found.
            w = inter * 0.67
            h = inter * 0.5
            draw.ellipse(
                [note.x - w, note.y - h, note.x + w, note.y + h],
                outline=colour,
                width=max(1, int(inter * 0.14)),
            )
            # A stem, in the direction the reader found.
            if note.stem:
                length = inter * 3.0
                draw.line(
                    [
                        (note.x, note.y),
                        (note.x, note.y + note.stem * length),
                    ],
                    fill=colour,
                    width=max(1, int(inter * 0.12)),
                )
            # A label with the duration.
            label = note.duration[0].upper()
            if note.dotted:
                label += "."
            draw.text((note.x - 4, note.y - inter * 0.95), label, fill=colour)
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    omr_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    clef = "treble"
    if "--clef" in sys.argv:
        clef = sys.argv[sys.argv.index("--clef") + 1]
    image_path = None
    if "--image" in sys.argv:
        image_path = Path(sys.argv[sys.argv.index("--image") + 1])

    staves, systems = read_omr(omr_path)

    if image_path is None:
        import io
        import zipfile

        with zipfile.ZipFile(omr_path) as z:
            name = next(n for n in z.namelist() if n.endswith("BINARY.png"))
            img = Image.open(io.BytesIO(z.read(name)))
    else:
        img = Image.open(image_path)
    gray = np.asarray(img.convert("L"))
    ink = gray < 128

    read = read_page(ink, staves, systems, clef=clef)
    total = 0
    for entry in read:
        notes = entry["notes"]
        total += len(notes)
        print(
            f"system {entry['system']}: {len(notes)} notes, "
            f"{len(entry['barlines'])} barlines, interline {entry['staff'].interline:.1f}"
        )
        for note in notes[:40]:
            print(
                f"    x={note.x:7.1f} {note.pitch_name():4s} {note.duration:8s}"
                f"{' dot' if note.dotted else '':4s} stem={note.stem:+d} beams={note.beams}"
                f"{' hollow' if note.hollow else ''}"
            )
    print(f"total notes: {total}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    draw_overlay(img, read).save(out_path)
    print(f"overlay -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
