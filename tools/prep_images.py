#!/usr/bin/env python3
"""Prepare scanned score pages for OMR.

The four scores arrive in two different shapes, and both need untangling before
an OMR engine can read them:

  Besame Mucho   a screen capture of a score editor: clean black on white, one
                 treble staff, but carrying the editor's application header
                 (close button, file title, view icons) at the top.

  The other three photographs of a printed, spiral-bound book: two or three
                 staves a system, lyrics, chord symbols, a publisher banner
                 across the top, and a slight tilt from the camera.

So the pipeline is, in order:

  1. crop to ink            drop the A4 margin, keeping the book page / capture
  2. remove solid banners   a publisher header is a filled bar; left in place an
                            OMR engine reads it as a stack of staff lines
  3. strip the app header   the editor's title bar, again not music
  4. deskew                 level the staves, so line detection can assume level
  5. normalise              stretch to a clean bimodal black-on-white image
  6. upscale                the capture is small; scale up so the interline
                            lands in the range the OMR engine is happy with

Steps 1-4 are measured from the image, never assumed.  Every step records what
it did in the report so a bad page is visible rather than silent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

MIN_HEADER_GAP = 16
MAX_HEADER_HEIGHT = 140
BANNER_FILL = 0.50


def otsu_threshold(gray: np.ndarray) -> int:
    """Otsu's threshold on a greyscale array.

    Three of the four scores are photographs of printed pages on grey paper, so
    a fixed cut-off cannot separate ink from paper: everything is "dark".  The
    threshold has to come from the page's own histogram.
    """
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 128
    levels = np.arange(256)
    w0 = np.cumsum(hist)
    w1 = total - w0
    cum = np.cumsum(hist * levels)
    mean_total = cum[-1]
    valid = (w0 > 0) & (w1 > 0)
    if not valid.any():
        return 128
    variance = np.zeros(256)
    variance[valid] = (
        (mean_total * w0[valid] / total - cum[valid]) ** 2 / (w0[valid] * w1[valid])
    )
    return int(np.argmax(variance))


def crop_to_ink(img: Image.Image, threshold: int = 240, margin: int = 10) -> Image.Image:
    a = np.asarray(img.convert("L"))
    ys, xs = np.nonzero(a < threshold)
    if len(xs) == 0:
        return img
    x0 = max(0, int(xs.min()) - margin)
    x1 = min(img.width, int(xs.max()) + margin)
    y0 = max(0, int(ys.min()) - margin)
    y1 = min(img.height, int(ys.max()) + margin)
    return img.crop((x0, y0, x1, y1))


def remove_banners(img: Image.Image) -> tuple[Image.Image, list[dict]]:
    """Cut filled horizontal bars off the top and bottom of a page.

    A staff line is black and spans most of the page width, so "is this row
    dark" cannot tell a banner from music.  Thickness can: a staff line is one
    or two rows tall, a publisher banner is dozens.  The 印象 banner is also a
    gradient rather than a flat black bar, which is why the test is on the
    fraction of each row that is ink rather than on its exact darkness.
    """
    gray = np.asarray(img.convert("L"))
    ink = gray < otsu_threshold(gray)
    if ink.size == 0:
        return img, []
    width = ink.shape[1]
    fill = ink.mean(axis=1)

    bands = []
    y = 0
    n = len(fill)
    while y < n:
        if fill[y] >= BANNER_FILL:
            start = y
            while y < n and fill[y] >= BANNER_FILL:
                y += 1
            bands.append((start, y))
        else:
            y += 1

    # Only a band several rows thick is furniture; anything thinner is music.
    thick = [b for b in bands if b[1] - b[0] >= 8]
    if not thick:
        return img, []

    removed = []
    cut_top = 0
    cut_bottom = img.height
    # A banner may start a little way down the page, because its top edge fades
    # in; what makes it a banner is that it is a thick band, not that it starts
    # at the very first row.
    if thick[0][0] <= 60 and (thick[0][1] - thick[0][0]) >= 12:
        cut_top = thick[0][1]
        removed.append({"band": list(thick[0]), "edge": "top"})
    if thick[-1][1] >= n - 60 and (thick[-1][1] - thick[-1][0]) >= 12:
        cut_bottom = thick[-1][0]
        removed.append({"band": list(thick[-1]), "edge": "bottom"})

    out = img.crop((0, cut_top, width, cut_bottom))
    return out, removed


def strip_header(img: Image.Image) -> tuple[Image.Image, dict]:
    """Cut a short first block of ink that is separated from the music."""
    a = np.asarray(img.convert("L"))
    ink = (a < 245).sum(axis=1)
    if not ink.any():
        return img, {"action": "none"}

    first = int(np.nonzero(ink)[0][0])
    block_end = first
    y = first
    while y < len(ink):
        if ink[y] == 0:
            gap = 0
            while y < len(ink) and ink[y] == 0:
                gap += 1
                y += 1
            if gap >= MIN_HEADER_GAP:
                block_end = y - gap
                break
            continue
        y += 1
    else:
        block_end = len(ink)

    height = block_end - first
    if 0 < height <= MAX_HEADER_HEIGHT and block_end < len(ink):
        out = img.crop((0, block_end, img.width, img.height))
        return out, {"action": "stripped", "height": height, "cut_row": block_end}
    return img, {"action": "kept", "height": height}


def estimate_skew(img: Image.Image, limit: float = 4.0) -> float:
    """Angle in degrees by which the image must rotate to level the staves.

    The staff lines are isolated first — a horizontal opening keeps only runs
    of ink that are long and thin — and the page's tilt is the median tilt of
    those runs, measured by the principal axis of each one.

    The lines have to be found with a *faint* ink test, not the usual one.  On
    the printed pages the staff lines are only about fifteen grey levels below
    the paper while the notes are a hundred and fifty below it, so an ordinary
    threshold finds the notes and misses the lines entirely — and the lines are
    exactly what the tilt has to be measured from.  Miss the lines and the page
    looks level when it is not; every later measurement that assumes level
    staff lines (pitch, bar positions) then inherits that error.
    """
    from scipy import ndimage

    gray = np.asarray(img.convert("L")).astype(np.float64)
    paper = ndimage.median_filter(gray, size=(13, 1))
    faint = (paper - gray) > 7
    if faint.sum() < 500:
        return 0.0

    width = faint.shape[1]
    # Long enough to survive a staff line, short enough to keep beams out.
    run_len = max(15, int(width * 0.22))
    horiz = ndimage.binary_opening(faint, structure=np.ones((1, run_len)))
    horiz = ndimage.binary_closing(horiz, structure=np.ones((3, 3)))

    labels, n = ndimage.label(horiz)
    if n == 0:
        return 0.0

    angles = []
    for sl in ndimage.find_objects(labels):
        block = horiz[sl]
        ys, xs = np.nonzero(block)
        if len(xs) < run_len:
            continue
        # Longest extent decides whether this is a line or a filled block.
        if xs.max() - xs.min() < run_len * 0.9:
            continue
        if ys.max() - ys.min() > 14:  # a banner or a block of text, not a line
            continue
        # Principal axis of the run: its tilt is the tilt of that staff line.
        cx, cy = xs.mean(), ys.mean()
        cov = np.cov(np.vstack([xs - cx, ys - cy]))
        if cov[0, 0] == 0:
            continue
        angle = 0.5 * np.arctan2(2 * cov[0, 1], cov[0, 0] - cov[1, 1])
        degrees = float(np.degrees(angle))
        if degrees > 45:
            degrees -= 90
        elif degrees < -45:
            degrees += 90
        angles.append(degrees)

    if len(angles) < 4:
        return 0.0

    angles = np.array(angles)
    median = float(np.median(angles))
    keep = angles[np.abs(angles - median) < 1.0]
    if len(keep) < 4:
        return median
    return float(np.median(keep))


def normalise(img: Image.Image) -> Image.Image:
    """Stretch to a clean bimodal black-on-white image."""
    a = np.asarray(img).astype(np.float32)
    lo = float(np.percentile(a, 0.5))
    hi = float(np.percentile(a, 99.5))
    if hi - lo < 20:
        lo, hi = 0.0, 255.0
    a = np.clip((a - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    return Image.fromarray(a)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--indir", required=True, type=Path)
    ap.add_argument("--outdir", required=True, type=Path)
    ap.add_argument("--scale", type=float, default=3.0)
    ap.add_argument("--border", type=int, default=40)
    ap.add_argument("--no-deskew", action="store_true")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    images = sorted(p for p in args.indir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
    if not images:
        print(f"no images in {args.indir}", file=sys.stderr)
        return 1

    report = []
    for src in images:
        img = ImageOps.exif_transpose(Image.open(src))
        steps = {}

        img = crop_to_ink(img)
        steps["cropped"] = list(img.size)

        img, banners = remove_banners(img)
        steps["banners"] = banners
        steps["after_banner"] = list(img.size)

        img, hdr = strip_header(img)
        steps["header"] = hdr

        skew = 0.0 if args.no_deskew else estimate_skew(img)
        if abs(skew) >= 0.15:
            img = img.rotate(skew, resample=Image.BICUBIC, expand=True, fillcolor=255)
        steps["skew_deg"] = round(skew, 2)

        img = crop_to_ink(img)
        img = normalise(img)

        if args.scale != 1.0:
            img = img.resize(
                (max(1, int(round(img.width * args.scale))), max(1, int(round(img.height * args.scale)))),
                Image.LANCZOS,
            )
        if args.border:
            canvas = Image.new("L", (img.width + 2 * args.border, img.height + 2 * args.border), 255)
            canvas.paste(img, (args.border, args.border))
            img = canvas

        dst = args.outdir / src.name
        img.save(dst)
        report.append({"source": str(src), "output": str(dst), "size": list(img.size), **steps})
        print(
            f"{src.name}: banners={len(banners)} header={hdr['action']} "
            f"skew={skew:+.2f} -> {img.size}"
        )

    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
