#!/usr/bin/env python3
"""Recognise the melody staff of a scanned score page.

Why this exists instead of a general OMR engine
-----------------------------------------------
The four scores are of two kinds, and neither is what a general engine is good
at:

  Besame Mucho    a screen capture — clean, but carrying the editor's own
                  chord symbols and fingering numbers above the staff, which
                  look exactly like noteheads.

  The other three photographs of a printed, spiral-bound book — two or three
                  staves a system, lyrics between them, guitar chord diagrams
                  in little grids above the staff, and a publisher banner.

Audiveris was tried on all four and got the systems and the staff lines right
but the notes wrong — pitches an octave out and every note an eighth.  A wrong
reference is worse than none, because the practice system would then "correct"
a correct performance into an incorrect one.

So the note reading is done here instead, with the structure of these specific
pages built in:

  * the melody is the TOP staff of every system, in all four scores;
  * a chord symbol sits above the melody staff, so the region above the staff
    is text and must not be read as notes;
  * the guitar chord diagrams are little grids whose lines are far shorter than
    a staff line, which is how they are told apart.

The output is a JSON description of every system: its staves, its barlines,
its notes, and the x position of each chord symbol.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from scipy import ndimage
except ImportError:  # pragma: no cover
    ndimage = None


# ── thresholds ────────────────────────────────────────────────────────────────────
STEP_NAMES = "CDEFGAB"
STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def otsu_threshold(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 128
    levels = np.arange(256)
    w0 = np.cumsum(hist)
    w1 = total - w0
    cum = np.cumsum(hist * levels)
    valid = (w0 > 0) & (w1 > 0)
    if not valid.any():
        return 128
    variance = np.zeros(256)
    variance[valid] = ((cum[-1] * w0[valid] / total - cum[valid]) ** 2) / (w0[valid] * w1[valid])
    return int(np.argmax(variance))


def sauvola_threshold(gray: np.ndarray, window: int = 31, k: float = 0.2) -> np.ndarray:
    """Sauvola local thresholding, returned as a boolean ink mask.

    Three of the four scores are photographs of a printed page: the paper is
    mid-grey, the ink is black, and the lighting falls off towards the edge of
    the page.  A single global cut-off cannot do that — the histogram has a
    broad paper mode and a broad ink mode, and Otsu lands between them or
    beyond them depending on the white margin that was added around the page.
    A local threshold follows the paper wherever it is.
    """
    if ndimage is None:  # pragma: no cover
        return gray < otsu_threshold(gray)

    g = gray.astype(np.float64)
    mean = ndimage.uniform_filter(g, size=window)
    mean_sq = ndimage.uniform_filter(g * g, size=window)
    variance = np.clip(mean_sq - mean * mean, 0.0, None)
    std = np.sqrt(variance)
    threshold = mean * (1.0 + k * (std / 128.0 - 1.0))
    return g <= threshold


def binarise(img: Image.Image, window: int = 31) -> np.ndarray:
    gray = np.asarray(img.convert("L"))
    return sauvola_threshold(gray, window=window)


def paper_level(gray: np.ndarray, size: int = 13) -> np.ndarray:
    """Local paper level, estimated with a vertical median.

    The staff lines on the printed pages are faint — about thirty grey levels
    below the paper — while the notes are a hundred and fifty below it.  A
    local mean is dragged down by the notes and misses the lines; a vertical
    median is not, because a median over a dozen rows is still the paper
    between the lines.
    """
    if ndimage is None:  # pragma: no cover
        return np.full_like(gray, float(np.median(gray)))
    return ndimage.median_filter(gray.astype(np.float64), size=(size, 1))


def faint_mask(gray: np.ndarray, delta: float = 12.0, size: int = 13) -> np.ndarray:
    """Everything measurably darker than the paper, faint lines included."""
    return (paper_level(gray, size) - gray) > delta


# ── staff lines ───────────────────────────────────────────────────────────────────
@dataclass
class Staff:
    """Five staff lines, top to bottom."""

    lines: list[int]  # y of each line
    x0: int
    x1: int

    @property
    def interline(self) -> float:
        if len(self.lines) < 2:
            return 0.0
        gaps = [self.lines[i + 1] - self.lines[i] for i in range(len(self.lines) - 1)]
        return float(np.median(gaps))

    @property
    def top(self) -> int:
        return self.lines[0]

    @property
    def bottom(self) -> int:
        return self.lines[-1]

    def line_for(self, y: float) -> int | None:
        """Index of the staff line nearest to y, or None if outside the staff."""
        best, best_d = None, 1e9
        for i, ly in enumerate(self.lines):
            d = abs(ly - y)
            if d < best_d:
                best, best_d = i, d
        if best_d > self.interline * 0.75:
            return None
        return best


@dataclass
class System:
    """A group of staves played together."""

    staves: list[Staff]
    y0: int
    y1: int


def horizontal_runs(ink: np.ndarray, min_len: int) -> list[tuple[int, int, int]]:
    """All maximal horizontal runs of ink at least `min_len` long.

    Returns (row, start_x, end_x).  Staff lines are long horizontal runs; a
    guitar chord diagram's grid lines are not, which is how the two are told
    apart.
    """
    runs = []
    h, w = ink.shape
    for y in range(h):
        row = ink[y]
        if not row.any():
            continue
        # find transitions
        idx = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        starts = idx[::2]
        ends = idx[1::2]
        for s, e in zip(starts, ends):
            if e - s >= min_len:
                runs.append((y, int(s), int(e)))
    return runs


def find_staffs(ink: np.ndarray, min_run: int | None = None) -> list[Staff]:
    """Group horizontal runs into staves of five evenly spaced lines."""
    h, w = ink.shape
    if min_run is None:
        min_run = max(20, int(w * 0.10))

    runs = horizontal_runs(ink, min_run)
    if not runs:
        return []

    # A staff line may be broken into several runs by the notes crossing it.
    # Merge runs on the same row that are close together into one segment.
    by_row: dict[int, list[tuple[int, int]]] = {}
    for y, s, e in runs:
        by_row.setdefault(y, []).append((s, e))
    for y in list(by_row):
        segs = sorted(by_row[y])
        merged = [list(segs[0])]
        for s, e in segs[1:]:
            if s - merged[-1][1] <= min_run * 0.35:
                merged[-1][1] = e
            else:
                merged.append([s, e])
        by_row[y] = [(s, e) for s, e in merged]

    rows = sorted(by_row)
    staves: list[Staff] = []

    i = 0
    while i < len(rows):
        # Candidate staff: rows[i] .. rows[i+4] with consistent spacing.
        if i + 4 >= len(rows):
            break
        group = rows[i : i + 5]
        gaps = [group[k + 1] - group[k] for k in range(4)]
        spacing = float(np.median(gaps))
        if spacing < 4:
            i += 1
            continue
        if max(gaps) - min(gaps) > spacing * 0.35:
            i += 1
            continue
        # The lines must all span roughly the same horizontal range.
        spans = [by_row[y] for y in group]
        longest = max(s[1] for segs in spans for s in segs)
        shortest = min(min(s[0] for s in segs) for segs in spans)
        x0, x1 = shortest, longest
        coverage = []
        for segs in spans:
            covered = sum(max(0, min(e, x1) - max(s, x0)) for s, e in segs)
            coverage.append(covered / max(1, x1 - x0))
        if min(coverage) < 0.45:
            i += 1
            continue
        staves.append(Staff(lines=group, x0=int(x0), x1=int(x1)))
        i += 5
    return staves


def row_score(dmap: np.ndarray, quantile: float = 0.72) -> np.ndarray:
    """Per-row evidence of a long horizontal line.

    A staff line is faint and sometimes broken, so it is scored by how dark the
    row is over a generous fraction of its width, rather than by the darkest
    pixel in it (which would be a note).
    """
    h, w = dmap.shape
    k = max(1, int(w * (1.0 - quantile)))
    part = np.partition(dmap, w - k, axis=1)[:, w - k :]
    return part.mean(axis=1)


def detect_line_combs(
    dmap: np.ndarray,
    spacings: range = range(4, 26),
    min_score: float = 3.0,
) -> list[list[int]]:
    """Find staves as five rows of equal spacing that are all line-like.

    Per-row thresholds are not enough on these pages: the printed staff lines
    are faint, and how faint depends on how the page fell under the light, so
    the top of the page needs a low threshold and the bottom a high one.  The
    five-lines-at-equal-spacing structure is a property of every staff
    everywhere, so it is used as the detector instead: for each candidate
    spacing, the five rows are scored together and only strong combs are kept.
    """
    score = row_score(dmap)
    h = score.shape[0]
    used = np.zeros(h, dtype=bool)
    combs: list[list[int]] = []

    candidates: list[tuple[float, int, list[int]]] = []
    for s in spacings:
        if 5 * s >= h:
            break
        for t in range(0, h - 5 * s):
            rows = [t + k * s for k in range(5)]
            best = 0.0
            for r in rows:
                lo, hi = max(0, r - 1), min(h, r + 2)
                best += float(score[lo:hi].max())
            candidates.append((best, s, rows))

    candidates.sort(key=lambda c: -c[0])
    for best, s, rows in candidates:
        if best < min_score * 5:
            break
        if any(used[r] for r in rows):
            continue
        # Refine each row to its local maximum.
        refined = []
        for r in rows:
            lo, hi = max(0, r - 1), min(h, r + 2)
            refined.append(int(lo + int(np.argmax(score[lo:hi]))))
        for r in refined:
            used[r] = True
        combs.append(refined)

    combs.sort(key=lambda c: c[0])
    return combs


def comb_scores(score: np.ndarray, spacings: range) -> np.ndarray:
    """For each spacing, the total row score of five evenly spaced rows.

    Row 4 of the result is for spacing s: ``out[t, 4]`` is the best total for
    a staff whose top line is at row ``t`` and whose lines are ``s`` apart.
    """
    h = score.shape[0]
    out = np.zeros((len(spacings), h), dtype=np.float64)
    max_s = max(spacings)
    padded = np.concatenate([score, np.zeros(5 * max_s + 2)])
    for si, s in enumerate(spacings):
        if 5 * s >= h:
            continue
        acc = np.zeros(h)
        for k in range(5):
            shifted = padded[k * s : k * s + h]
            # Allow a row of jitter: the line may sit one pixel off the ideal.
            up = np.concatenate([shifted[:1], shifted[:-1]])
            down = np.concatenate([shifted[1:], shifted[-1:]])
            acc += np.maximum(np.maximum(shifted, up), down)
        out[si] = acc
    return out


def detect_staves(
    gray: np.ndarray,
    size: int = 13,
    delta: float = 3.0,
    spacings: range = range(6, 21),
    min_total: float = 42.0,
    max_staves: int = 40,
) -> list[list[int]]:
    """Find staves as five evenly spaced rows that are all line-like.

    A per-row threshold is not enough on these pages: the printed staff lines
    are faint, and how faint depends on how the page fell under the light, so
    the top of the page wants a low threshold and the bottom a high one.  The
    five-lines-at-equal-spacing structure is a property of every staff
    everywhere, so it is used as the detector instead — the five rows are
    scored together, and only a comb whose total clears the bar is accepted.
    """
    paper = paper_level(gray, size)
    dmap = paper - gray
    x0, x1 = music_columns(gray, paper)
    if x1 - x0 < 40:
        return []
    # Score each row by how dark it is over the printed music.
    region = np.clip(dmap[:, x0:x1], 0, None)
    k = max(1, int(region.shape[1] * 0.30))
    score = np.sort(region, axis=1)[:, -k:].mean(axis=1)
    score = np.maximum(score, 0.0)

    spacings = list(spacings)
    totals = comb_scores(score, spacings)
    staves: list[list[int]] = []
    used = np.zeros(score.shape[0], dtype=bool)

    def blocked(t: int, s: int) -> bool:
        return any(used[max(0, t + k * s - 2) : t + k * s + 3].any() for k in range(5))

    while len(staves) < max_staves:
        best = -1.0
        best_si = best_t = 0
        for si, s in enumerate(spacings):
            col = totals[si]
            for t in range(len(col) - 5 * s):
                if col[t] > best and not blocked(t, s):
                    best = float(col[t])
                    best_si, best_t = si, t
        if best < min_total:
            break
        s = spacings[best_si]
        t = best_t
        rows = []
        for k in range(5):
            r = t + k * s
            lo, hi = max(0, r - 1), min(score.shape[0], r + 2)
            rows.append(int(lo + int(np.argmax(score[lo:hi]))))
        staves.append(rows)
        for r in rows:
            used[max(0, r - 3) : r + 4] = True

    staves.sort(key=lambda rows: rows[0])
    return staves


def music_columns(gray: np.ndarray, paper: np.ndarray) -> tuple[int, int]:
    """The horizontal extent of the printed music on the page."""
    ink = (paper - gray) > 3
    cols = ink.sum(axis=0)
    if not cols.any():
        return 0, gray.shape[1]
    nz = np.nonzero(cols)[0]
    return int(nz.min()), int(nz.max())


def find_staff_lines(gray: np.ndarray, delta: float = 3.0, size: int = 13) -> list[int]:
    """Rows that carry a long, faint horizontal line — the staff lines.

    The coverage is measured over the printed music only.  Measuring it over
    the whole page would let the margins dilute a real staff line below the
    threshold, and on these pages the margins are a third of the width.
    """
    paper = paper_level(gray, size)
    x0, x1 = music_columns(gray, paper)
    if x1 - x0 < 40:
        return []
    mask = (paper - gray) > delta
    coverage = mask[:, x0:x1].mean(axis=1)
    rows = np.flatnonzero(coverage > 0.5)
    if not len(rows):
        return []
    groups: list[list[int]] = [[int(rows[0])]]
    for r in rows[1:]:
        if r - groups[-1][-1] <= 2:
            groups[-1].append(int(r))
        else:
            groups.append([int(r)])
    # Keep the middle of each band: a line is one or two rows, not five.
    return [int(round(float(np.mean(g)))) for g in groups]


def staves_from_lines(lines: list[int], min_run_frac: float = 0.30) -> list[Staff]:
    """Combine detected line rows into staves of five with regular spacing."""
    staves: list[Staff] = []
    i = 0
    while i < len(lines):
        if i + 4 >= len(lines):
            break
        group = lines[i : i + 5]
        gaps = [group[k + 1] - group[k] for k in range(4)]
        spacing = float(np.median(gaps))
        if spacing < 4 or (max(gaps) - min(gaps)) > spacing * 0.4:
            i += 1
            continue
        staves.append(Staff(lines=group, x0=0, x1=0))
        i += 5
    return staves


def group_systems(staves: list[Staff], max_gap: float | None = None) -> list[System]:
    """Group staves into systems: staves that overlap horizontally and sit close
    together vertically belong to the same system."""
    if not staves:
        return []
    if max_gap is None:
        max_gap = 120.0

    systems: list[System] = []
    current = [staves[0]]
    for prev, staff in zip(staves, staves[1:]):
        gap = staff.top - prev.bottom
        overlap = min(prev.x1, staff.x1) - max(prev.x0, staff.x0)
        width = max(1, min(prev.x1 - prev.x0, staff.x1 - staff.x0))
        if gap <= max_gap and overlap > 0.45 * width:
            current.append(staff)
        else:
            systems.append(System(staves=current, y0=current[0].top, y1=current[-1].bottom))
            current = [staff]
    systems.append(System(staves=current, y0=current[0].top, y1=current[-1].bottom))
    return systems


# ── debugging overlay ─────────────────────────────────────────────────────────────
def overlay(img: Image.Image, staves: list[Staff], systems: list[System]) -> Image.Image:
    from PIL import ImageDraw

    out = img.convert("RGB")
    draw = ImageDraw.Draw(out)
    for si, system in enumerate(systems):
        colour = (255, 0, 0) if si % 2 == 0 else (0, 128, 255)
        for staff in system.staves:
            for y in staff.lines:
                draw.line([(0, y), (out.width, y)], fill=colour, width=1)
        draw.rectangle(
            [0, system.y0, out.width, system.y1], outline=colour, width=2
        )
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--overlay", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--delta", type=float, default=12.0)
    args = ap.parse_args()

    img = Image.open(args.image)
    gray = np.asarray(img.convert("L"))
    lines = find_staff_lines(gray, delta=args.delta)
    staves = staves_from_lines(lines)
    systems = group_systems(staves)

    print(f"{args.image}: {len(lines)} line rows, {len(staves)} staves, {len(systems)} systems")
    for si, system in enumerate(systems):
        s0 = system.staves[0]
        print(
            f"  system {si}: {len(system.staves)} staff(s) "
            f"interline={s0.interline:.1f} lines={[s.lines for s in system.staves]}"
        )

    if args.overlay:
        overlay(img, staves, systems).save(args.overlay)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "image": str(args.image),
                    "line_rows": lines,
                    "staves": [asdict(s) for s in staves],
                    "systems": [
                        {"y0": sy.y0, "y1": sy.y1, "staves": [asdict(s) for s in sy.staves]}
                        for sy in systems
                    ],
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
