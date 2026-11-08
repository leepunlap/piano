#!/usr/bin/env python3
"""Read the melody notes off a binarised score.

Why not Audiveris
-----------------
Audiveris finds the staves on these pages perfectly — it even tracks the warp
of a photographed page, line by line.  What it cannot do is read the notes,
because a score page is full of things that are not notes: chord symbols,
guitar chord diagrams, lyrics, fingering numbers, rehearsal marks.  It reads
them as notes and the result is worse than nothing.

So the notes are read here instead, on a binarised page where the clutter has
already been taken away and the staff lines are known exactly.

How a note is found
-------------------
A notehead is a filled ellipse, about one and a third interlines wide and one
interline tall.  Everything else that could be mistaken for one is not: a stem
is a thin vertical line, a beam is a thick horizontal bar, a sharp is a pair
of strokes, a rest is a squiggle.  So for every position the head could be at
— which is not any pixel but one of the half-space positions the staff allows
— the page is asked how much of an ellipse of that size is ink.

A notehead fills its ellipse; nothing else does.  That one measurement
separates the music from everything that merely resembles it.

How long a note lasts
---------------------
A hollow head is a half or a whole note.  A filled head with a beam is an
eighth, a sixteenth or a thirty-second, and the number of beams says which.  A
filled head with a stem and no beam is a quarter, unless it carries a flag, in
which case it is an eighth.  A dot after the head makes it dotted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

try:
    from scipy import ndimage
except ImportError:  # pragma: no cover
    ndimage = None


LETTERS = "CDEFGAB"
# Where the bottom staff line sits, as a diatonic number (C0 = 0).
BOTTOM_LINE_DIATONIC = {"treble": 4 * 7 + 2, "bass": 2 * 7 + 4, "alto": 3 * 7 + 3}

DURATION_BEATS = {
    "whole": 4.0,
    "half": 2.0,
    "quarter": 1.0,
    "eighth": 0.5,
    "16th": 0.25,
    "32nd": 0.125,
    "64th": 0.0625,
}

BEAM_DURATION = {1: "eighth", 2: "16th", 3: "32nd", 4: "64th"}


@dataclass
class Note:
    """One notehead, with the time it takes up."""

    x: float
    y: float
    diatonic: int
    duration: str = "quarter"
    dotted: bool = False
    stem: int = 0  # -1 down, +1 up, 0 none
    beams: int = 0
    flags: int = 0
    hollow: bool = False
    rest: bool = False

    @property
    def beats(self) -> float:
        return DURATION_BEATS.get(self.duration, 1.0) * (1.5 if self.dotted else 1.0)

    @property
    def letter(self) -> str:
        return LETTERS[self.diatonic % 7]

    @property
    def octave(self) -> int:
        return self.diatonic // 7

    def pitch_name(self, alter: int = 0) -> str:
        acc = "#" * alter if alter > 0 else "b" * (-alter)
        return f"{self.letter}{acc}{self.octave}"


@dataclass
class Measure:
    number: int
    notes: list[Note] = field(default_factory=list)
    duration_beats: float = 4.0


# ── shape measurement ────────────────────────────────────────────────────────────
def _ellipse(interline: float, scale: float = 1.0) -> np.ndarray:
    h = max(2, int(round(interline * scale)))
    w = max(3, int(round(interline * 1.35 * scale)))
    yy, xx = np.mgrid[0 : 2 * h + 1, 0 : 2 * w + 1]
    a = (w / 2.0) ** 2
    b = (h / 2.0) ** 2
    return (((xx - w) ** 2) / a + ((yy - h) ** 2) / b <= 1.0).astype(np.float64)


def _ring(interline: float) -> np.ndarray:
    """The rim of a notehead: the whole ellipse less its middle.

    The two ellipses are drawn on one canvas, the smaller one centred in the
    larger, so the rim is a band of the right width all the way round.
    """
    outer = _ellipse(interline, 1.0)
    inner = _ellipse(interline, 0.72)
    h, w = outer.shape
    ih, iw = inner.shape
    canvas = np.zeros_like(outer)
    top = (h - ih) // 2
    left = (w - iw) // 2
    canvas[top : top + ih, left : left + iw] = inner
    return np.clip(outer - canvas, 0.0, 1.0)


def fill_map(ink: np.ndarray, interline: float, scale: float = 1.0) -> np.ndarray:
    """For every pixel, the fraction of a notehead-sized ellipse that is ink."""
    template = _ellipse(interline, scale)
    if ndimage is None:  # pragma: no cover
        return ink.astype(np.float64)
    conv = ndimage.correlate(ink.astype(np.float64), template, mode="constant", cval=0.0)
    return conv / template.sum()


def ring_map(ink: np.ndarray, interline: float) -> np.ndarray:
    """For every pixel, the fraction of a notehead's rim that is ink."""
    template = _ring(interline)
    conv = ndimage.correlate(ink.astype(np.float64), template, mode="constant", cval=0.0)
    return conv / max(1.0, template.sum())


# ── noteheads ───────────────────────────────────────────────────────────────────
def row_runs(row: np.ndarray) -> list[tuple[int, int]]:
    """The ink runs in one row, as (start, end) pairs."""
    idx = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
    return [(int(a), int(b)) for a, b in zip(idx[::2], idx[1::2])]


def head_span(runs: list[tuple[int, int]], x: int, max_gap: int) -> tuple[int, int] | None:
    """The horizontal extent of the thing at x, whether it is solid or an outline.

    A filled notehead has ink at x, so the run containing x is its extent.  A
    hollow one has a hole at x, so the extent is from the run on the left to
    the run on the right, provided the hole between them is narrow.
    """
    for s, e in runs:
        if s <= x <= e:
            return (s, e)
    left = None
    right = None
    for s, e in runs:
        if e < x and (left is None or e > left[1]):
            left = (s, e)
        if s > x and (right is None or s < right[0]):
            right = (s, e)
    if left and right and right[0] - left[1] <= max_gap:
        return (left[0], right[1])
    return None


def detect_heads(
    ink: np.ndarray,
    staff,
    clef: str = "treble",
    solid_threshold: float = 0.55,
    core_threshold: float = 0.72,
    hollow_solid: float = 0.30,
    hollow_core: float = 0.38,
    hollow_ring: float = 0.24,
    min_width: float = 0.80,
    max_width: float = 1.90,
) -> list[Note]:
    """Find the noteheads on one staff.

    Three measurements are made at every position on the staff's grid of
    half-spaces.  *Solid* asks how much of a whole notehead-sized ellipse is
    ink.  *Core* asks the same of a smaller ellipse at the centre.  *Ring*
    asks how much of the notehead's rim is ink.

    A filled notehead is solid all the way through: its core is full.  A
    hollow one is solid at the rim and empty in the middle: its core is empty
    and its ring is present.  A rest is neither — it is a squiggle or a
    rectangle — so it has neither a full core nor a clean ring.

    And then there is the width.  A notehead is about one and a third
    interlines across, and that is the measurement that settles the cases the
    shape leaves open: a beam is much wider than a notehead, a rest is much
    narrower, and a stem is neither.  The width is measured on the page with
    the staff lines taken out, because a notehead sitting on a line would
    otherwise be measured as wide as the whole staff.

    The staff lines are left in for the shape measurements, where they help:
    a head on a line is solid in the middle, a head in a space is solid at
    its rim only, and that difference is exactly what separates the two kinds
    of head.
    """
    inter = staff.interline
    if inter <= 0:
        return []
    solid = fill_map(ink, inter, 1.0)
    core = fill_map(ink, inter, 0.55)
    ring = ring_map(ink, inter)
    h, w = ink.shape
    bare = remove_staff_lines(ink, staff)

    bottom = BOTTOM_LINE_DIATONIC.get(clef, BOTTOM_LINE_DIATONIC["treble"])
    max_k = 13

    x0 = max(0, int(staff.left))
    x1 = min(w - 1, int(staff.right))
    # The clef, the key signature and the time signature are not notes.
    if staff.header_stop:
        x0 = max(x0, int(staff.header_stop))

    # Which grid positions carry a head at each column?
    columns: dict[int, dict[int, float]] = {}
    run_cache: dict[int, list[tuple[int, int]]] = {}
    for x in range(x0, x1 + 1):
        base = staff.line_y(4, x)
        found: dict[int, float] = {}
        for k in range(0, max_k + 1):
            y = base - k * inter / 2.0
            yi = int(round(y))
            if yi < 0 or yi >= h:
                continue
            s = float(solid[yi, x])
            c = float(core[yi, x])
            if s >= solid_threshold and c >= core_threshold:
                pass  # a filled head
            elif s >= hollow_solid and c <= hollow_core and float(ring[yi, x]) >= hollow_ring:
                pass  # a hollow head
            else:
                continue
            # How wide is the thing?  A notehead is about one and a third
            # interlines; a beam is wider and a rest is narrower.  The width
            # is measured on the page with the staff lines taken out, and on
            # the rows just off the head's centre, so that a head sitting on
            # a line is not measured as wide as the whole staff.
            width = 0.0
            reach = max(2, int(inter * 0.35))
            for dy in range(-reach, reach + 1):
                row = yi + dy
                if row < 0 or row >= h:
                    continue
                if row not in run_cache:
                    run_cache[row] = row_runs(bare[row])
                span = head_span(run_cache[row], x, int(inter * 0.9))
                if span is not None:
                    width = max(width, span[1] - span[0])
            if width < inter * min_width or width > inter * max_width:
                continue
            # A head is the top of a peak in the pitch direction.
            peak = True
            for kk in (k - 1, k + 1):
                yk = base - kk * inter / 2.0
                yki = int(round(yk))
                if 0 <= yki < h and float(solid[yki, x]) > s + 0.04:
                    peak = False
                    break
            if peak:
                found[k] = s
        if found:
            columns[x] = found

    # Group columns that belong to the same head.
    runs: dict[int, list[int]] = {}
    notes: list[Note] = []
    for x in range(x0, x1 + 1):
        here = set(columns.get(x, {}))
        for k in list(runs):
            if k not in here:
                note = _make_note(staff, runs.pop(k), k, bottom, inter, core)
                if note is not None:
                    notes.append(note)
        for k in here:
            runs.setdefault(k, []).append(x)
    for k, run in runs.items():
        note = _make_note(staff, run, k, bottom, inter, core)
        if note is not None:
            notes.append(note)

    notes.sort(key=lambda n: n.x)
    return notes


def _make_note(
    staff,
    run: list[int],
    k: int,
    bottom: int,
    inter: float,
    core: np.ndarray,
) -> Note | None:
    if len(run) < 4:
        return None
    x = sum(run) / len(run)
    y = staff.line_y(4, x) - k * inter / 2.0
    yi = int(round(y))
    if yi < 0 or yi >= core.shape[0]:
        return None
    # A hollow head is an outline: its core is empty.
    hollow = float(core[yi, int(round(x))]) < 0.38
    return Note(x=x, y=y, diatonic=bottom + k, hollow=hollow)


# ── stems, beams, flags, dots ───────────────────────────────────────────────────
def detect_stems(ink: np.ndarray, notes: list[Note], staff, inter: float) -> None:
    """Set each note's stem direction.

    The stems are found first and the heads are attached to them afterwards,
    rather than the other way round.  A stem is an unbroken vertical run of
    ink; a head is not, so looking for stems first and asking which head sits
    at the end of each one does not depend on knowing exactly where the
    centre of a head is — which is the measurement that is hardest to get
    right, because a head is an ellipse sitting between the staff lines and
    its true centre is a fraction of an interline away from any grid
    position.

    A rest's squiggle is not a straight run, and a fingering digit's curve is
    not either, so neither is mistaken for a stem.
    """
    h, w = ink.shape
    min_len = max(5, int(inter * 1.5))
    reach = max(1, int(inter * 0.45))
    for note in notes:
        xi = int(round(note.x))
        best = (0, 0)
        for dx in range(-reach, reach + 1):
            x = xi + dx
            if x < 0 or x >= w:
                continue
            column = ink[:, x]
            y = 0
            while y < h:
                if column[y]:
                    start = y
                    while y < h and column[y]:
                        y += 1
                    end = y
                    if end - start < min_len:
                        continue
                    # A head sits at one end of the run, and the stem goes the
                    # other way.  In image coordinates, y grows downwards.
                    if abs(start - note.y) <= inter * 0.6:
                        if end - start > best[0]:
                            best = (end - start, 1)
                    if abs(end - note.y) <= inter * 0.6:
                        if end - start > best[0]:
                            best = (end - start, -1)
                else:
                    y += 1
        note.stem = best[1] if best[0] else 0


def remove_beams(ink: np.ndarray, beams, inter: float) -> np.ndarray:
    """A copy of the page with the beam bars taken out.

    A beam is a long, thick, horizontal bar, and a notehead-sized ellipse laid
    across one is nearly as full as a notehead.  So the beams have to go
    before the noteheads can be found; a notehead under a beam is not part of
    the beam and is unaffected.

    Which horizontal bars are beams is decided by their thickness.  A staff
    line is long but thin; a beam is long and thick.  So the bars are found by
    eroding away everything that is not both, and the staff lines fall out on
    their own without needing to be told where they are.
    """
    if ndimage is None:  # pragma: no cover
        return ink
    out = ink.copy()
    long_run = max(3, int(inter * 1.6))
    thick = max(2, int(inter * 0.25))
    seed = ndimage.binary_erosion(ink, structure=np.ones((1, long_run)))
    seed = ndimage.binary_erosion(seed, structure=np.ones((thick, 1)))
    if not seed.any():
        return out
    bars = ndimage.binary_dilation(seed, structure=np.ones((1, long_run)))
    out[bars] = False
    return out


def remove_staff_lines(ink: np.ndarray, staff) -> np.ndarray:
    """A copy of the page with the staff lines taken out.

    The lines are known exactly — their positions were measured — so they can
    be removed precisely.  This matters for the parts of the page where a long
    horizontal bar is meaningful: without it the staff lines look like beams,
    and the clef looks like hundreds of them.
    """
    out = ink.copy()
    h, w = ink.shape
    half = max(1, int(round(staff.interline * 0.09)))
    x0 = max(0, int(staff.left))
    x1 = min(w, int(staff.right))
    for index in range(len(staff.lines)):
        for x in range(x0, x1):
            y = int(round(staff.line_y(index, x)))
            for dy in range(-half, half + 1):
                yy = y + dy
                if 0 <= yy < h:
                    out[yy, x] = False
    return out


def find_beams(ink: np.ndarray, staff, inter: float) -> list[tuple[float, float, int]]:
    """Horizontal beam bars, as (x_start, x_end, y).

    The input must already have the staff lines removed: a staff line is a
    long horizontal bar too, and there are five of them per staff.
    """
    h, w = ink.shape
    x0 = max(0, int(staff.left))
    x1 = min(w, int(staff.right))
    if staff.header_stop:
        x0 = max(x0, int(staff.header_stop))
    run_len = max(8, int(inter * 1.4))
    horiz = ndimage.binary_opening(ink[:, x0:x1], structure=np.ones((1, run_len)))
    top = staff.line_y(0, staff.left)
    bottom = staff.line_y(4, staff.left)
    beams: list[tuple[float, float, int]] = []
    labels, n = ndimage.label(horiz)
    for sl in ndimage.find_objects(labels):
        if sl is None:
            continue
        ys, xs = np.nonzero(horiz[sl])
        if len(xs) < run_len:
            continue
        thickness = ys.max() - ys.min() + 1
        if thickness < inter * 0.18 or thickness > inter * 0.85:
            continue
        by = float(np.median(ys)) + sl[0].start
        if by < top - inter * 3.0 or by > bottom + inter * 3.0:
            continue
        beams.append((float(xs.min() + x0), float(xs.max() + x0), int(by)))
    return beams


def count_beams(beams, note: Note, inter: float) -> int:
    """How many beams sit over this note.

    Beams come in stacks: a sixteenth has two bars, a thirty-second has
    three, and they are drawn parallel a fraction of an interline apart.  So
    the beams over one note are grouped by how close together they are, and
    the largest group is the answer.
    """
    if not beams:
        return 0
    x = note.x
    near = [by for bx0, bx1, by in beams if bx0 - inter * 0.8 <= x <= bx1 + inter * 0.8]
    if not near:
        return 0
    near.sort()
    best = 1
    run = 1
    for i in range(1, len(near)):
        if near[i] - near[i - 1] <= inter * 0.75:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best


def detect_flags(ink: np.ndarray, note: Note, inter: float) -> int:
    """Count the flags on an unbeamed note.

    A flag is a small curl at the far end of the stem.  One flag is an eighth,
    two a sixteenth, and the curl grows with each, so the vertical extent of
    the blob is what distinguishes them.
    """
    if note.stem == 0:
        return 0
    h, w = ink.shape
    xi = int(round(note.x))
    tip = int(round(note.y)) + note.stem * int(inter * 3.2)
    lo = min(int(round(note.y)), tip)
    hi = max(int(round(note.y)), tip)
    window = ink[max(0, lo - 2) : min(h, hi + 3), max(0, xi - 2) : min(w, xi + 4)]
    if window.size == 0:
        return 0
    rows = window.sum(axis=1)
    extent = 0
    for i, v in enumerate(rows):
        if v:
            extent = i + 1
    if extent < inter * 0.45:
        return 0
    return 1 if extent < inter * 1.15 else 2


def detect_dots(ink: np.ndarray, note: Note, inter: float) -> bool:
    """Is there an augmentation dot just to the right of the head?"""
    h, w = ink.shape
    for dx in (inter * 0.85, inter * 1.05, inter * 1.25):
        xi = int(round(note.x + dx))
        yi = int(round(note.y))
        if 0 <= xi < w and 0 <= yi < h:
            patch = ink[max(0, yi - 2) : yi + 3, max(0, xi - 2) : xi + 3]
            if patch.size and patch.sum() >= 3:
                return True
    return False


def assign_durations(
    notes: list[Note],
    ink: np.ndarray,
    staff,
    inter: float,
    beams: list,
) -> None:
    """Give every note its duration, from its shape."""
    top_y = staff.line_y(0, staff.left)
    for note in notes:
        note.beams = count_beams(beams, note, inter)
        if note.hollow:
            note.duration = "half" if note.stem else "whole"
            continue
        if note.stem == 0:
            # A filled head with no stem is not a note: it is a rest.
            note.rest = True
            note.duration = "half"
            continue
        if note.beams:
            note.duration = BEAM_DURATION.get(note.beams, "eighth")
            continue
        note.flags = detect_flags(ink, note, inter)
        if note.flags >= 2:
            note.duration = "16th"
        elif note.flags == 1:
            note.duration = "eighth"
        else:
            note.duration = "quarter"
        note.dotted = detect_dots(ink, note, inter)


# ── measures ────────────────────────────────────────────────────────────────────
def find_barlines(ink: np.ndarray, staff, inter: float) -> list[float]:
    """Vertical lines that span the staff: the bar lines.

    A stem is as long as a bar line, so length alone will not do it.  What
    tells them apart is where they start: a bar line runs the whole height of
    the staff, from the top line to the bottom line, while a stem starts at a
    notehead in the middle of it.  So a column is a bar line when there is ink
    at every row from the top staff line to the bottom one.
    """
    h, w = ink.shape
    x0 = max(0, int(staff.left))
    x1 = min(w, int(staff.right))
    if staff.header_stop:
        x0 = max(x0, int(staff.header_stop))
    out: list[float] = []
    for x in range(x0, x1):
        top = int(round(staff.line_y(0, x)))
        bottom = int(round(staff.line_y(4, x)))
        if top < 0 or bottom >= h:
            continue
        column = ink[top - 1 : bottom + 2, x]
        if column.size == 0:
            continue
        # A bar line crosses the staff lines, so the crossing is where the two
        # meet and a gap there is expected.  A gap anywhere else means the
        # column is not a bar line but a stem or some other stroke.
        ok = True
        for y in range(top - 1, bottom + 2):
            if ink[y, x]:
                continue
            near_line = any(
                abs(y - int(round(staff.line_y(i, x)))) <= 2 for i in range(5)
            )
            if not near_line:
                ok = False
                break
        if not ok:
            continue
        out.append(float(x))
    merged: list[float] = []
    gap = max(2, int(inter * 0.18))
    for x in out:
        if merged and x - merged[-1] <= gap:
            continue
        merged.append(x)
    return merged
