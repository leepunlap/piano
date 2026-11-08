#!/usr/bin/env python3
"""Read the staff geometry Audiveris found, and cut out clean melody bands.

Audiveris's first pass over a whole page is good at one thing and one thing
only: finding the staves.  Its grid step finds every staff and every staff
line, and it models each line as a short polyline, so it copes with the
slight warp of a photographed page — the top line of one staff can fall by more
than a dozen pixels across its width, which is a whole interline.

What it cannot do is read the notes on these particular pages, because a score
page is full of things that are not notes: chord symbols, guitar chord
diagrams, lyrics, fingering numbers.  Audiveris has to look at all of it.

So the geometry is taken from the first pass and the notes are read in a
second pass, over a crop that contains one staff and nothing else.  The crop
is cut along the staff's own lines, so the chord symbols above and the lyrics
below are left out, and the reading problem becomes the easy one.

Usage:
    omr_bands.py <page.omr> <source.png> <outdir>
"""
from __future__ import annotations

import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import xml.etree.ElementTree as ET

from PIL import Image


@dataclass
class StaffGeom:
    """One staff: five lines, each given as a polyline of (x, y) points."""

    sid: int
    left: float
    right: float
    lines: list[list[tuple[float, float]]]
    header_start: float = 0.0
    header_stop: float = 0.0

    def line_y(self, index: int, x: float) -> float:
        """The y of staff line `index` at column x, by linear interpolation."""
        pts = self.lines[index]
        if x <= pts[0][0]:
            return pts[0][1]
        if x >= pts[-1][0]:
            return pts[-1][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                if x1 == x0:
                    return y0
                t = (x - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return pts[-1][1]

    @property
    def interline(self) -> float:
        """Mean distance between adjacent lines, measured at the left end."""
        x = self.left
        gaps = [
            self.line_y(i + 1, x) - self.line_y(i, x) for i in range(len(self.lines) - 1)
        ]
        gaps = [g for g in gaps if g > 0]
        return sum(gaps) / len(gaps) if gaps else 0.0

    def band(self, above: float = 3.2, below: float = 2.0) -> tuple[int, int, int, int]:
        """The crop box of this staff plus its stems and high notes.

        `above` and `below` are in interlines: stems reach about three and a
        half interlines, and a note an octave above the staff needs two more.
        """
        inter = self.interline
        xs = [p[0] for ln in self.lines for p in ln]
        ys_top = [self.line_y(0, x) for x in (self.left, self.right)]
        ys_bot = [self.line_y(len(self.lines) - 1, x) for x in (self.left, self.right)]
        x0 = int(min(xs)) - 8
        x1 = int(max(xs)) + 8
        y0 = int(min(ys_top) - above * inter)
        y1 = int(max(ys_bot) + below * inter)
        return (x0, y0, x1, y1)


def read_omr(path: Path) -> tuple[list[StaffGeom], dict[int, list[int]]]:
    """Return the staves in an Audiveris book, and the staff ids per system."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith("sheet#1.xml") or "/sheet" in n]
        name = next(n for n in z.namelist() if n.endswith(".xml") and "sheet" in n)
        root = ET.fromstring(z.read(name))

    staves: list[StaffGeom] = []
    for el in root.findall(".//staff"):
        lines = []
        for ln in el.findall(".//line"):
            pts = [(float(p.get("x")), float(p.get("y"))) for p in ln.findall("point")]
            if len(pts) >= 2:
                lines.append(pts)
        if len(lines) != 5:
            continue
        header = el.find("header")
        staves.append(
            StaffGeom(
                sid=int(el.get("id")),
                left=float(el.get("left")),
                right=float(el.get("right")),
                lines=lines,
                header_start=float(header.get("start", 0)) if header is not None else 0.0,
                header_stop=float(header.get("stop", 0)) if header is not None else 0.0,
            )
        )
    staves.sort(key=lambda s: s.sid)

    systems: dict[int, list[int]] = {}
    for sys_el in root.findall(".//system"):
        sid = int(sys_el.get("id"))
        ids = [int(s.get("id")) for s in sys_el.findall(".//staff")]
        if ids:
            systems[sid] = ids
    if not systems:
        # Fall back to pairing the staves in order.
        systems = {i // 2 + 1: [s.sid for s in staves[i : i + 2]] for i in range(0, len(staves), 2)}
    return staves, systems


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 1
    omr_path = Path(sys.argv[1])
    source = Path(sys.argv[2])
    outdir = Path(sys.argv[3])
    outdir.mkdir(parents=True, exist_ok=True)

    staves, systems = read_omr(omr_path)
    img = Image.open(source)
    print(f"{omr_path.name}: {len(staves)} staves, {len(systems)} systems, image {img.size}")

    by_id = {s.sid: s for s in staves}
    for sys_id, ids in sorted(systems.items()):
        ids = [i for i in ids if i in by_id]
        if not ids:
            continue
        # The melody is the top staff of the system: the one with the smallest y.
        top = min(ids, key=lambda i: by_id[i].line_y(0, by_id[i].left))
        others = [i for i in ids if i != top]
        box = by_id[top].band()
        crop = img.crop(box)
        name = f"sys{sys_id:02d}-melody.png"
        crop.save(outdir / name)
        print(
            f"  system {sys_id}: staves {ids} -> melody staff {top} "
            f"interline={by_id[top].interline:.1f} crop={box} size={crop.size}"
        )
        if others:
            for oid in others:
                obox = by_id[oid].band()
                img.crop(obox).save(outdir / f"sys{sys_id:02d}-staff{oid}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
