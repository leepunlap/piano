#!/usr/bin/env python3
"""Run Audiveris over one page, at whichever scale makes it work.

Audiveris's grid step needs the staff to be a sensible size: too small and
it cannot see the interline, too large and the lines blur into each other.
But the right size is not the same for every page.  A page of a spiral-bound
book photographed at an angle has a staff that curves away from the camera,
and a page that is straight and clean does not.

So each page is tried at a few scales, and the one that gives Audiveris a
*rhythm* is kept.  A page with heads but no rhythm is no use: without the
rhythm there is no bar, and without a bar there is no beat, and without a
beat there is no piece of music.

Usage:
    omr_page.py <page.png> <work-dir> [--scales 2.0,3.0,2.5]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def run_audiveris(image: Path, out_dir: Path) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ROOT / "tools" / "audiveris"),
        "-batch",
        "-export",
        "-output",
        str(out_dir),
        str(image),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None
    omr = out_dir / f"{image.stem}.omr"
    return omr if omr.exists() else None


def rhythm_score(omr: Path) -> int:
    """How much rhythm Audiveris managed to work out: the number of slots."""
    try:
        from omr_sheet import load_omr

        sheet = load_omr(omr)
        return sum(
            len(stack.findall("slot"))
            for system in sheet.page.findall("system")
            for stack in system.findall("stack")
        )
    except Exception:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("work")
    ap.add_argument("--scales", default="2.0,3.0,2.5")
    args = ap.parse_args()

    page = Path(args.page)
    work = Path(args.work)
    name = page.stem
    scales = [float(s) for s in args.scales.split(",") if s.strip()]

    best: tuple[int, Path] | None = None
    for index, scale in enumerate(scales):
        prep_dir = work / f"prep-{scale}"
        prep_dir.mkdir(parents=True, exist_ok=True)
        prepped = prep_dir / f"{name}.png"

        # Only the one page is prepared; the rest of the folder is ignored.
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "prep_images.py"),
                "--indir",
                str(page.parent),
                "--outdir",
                str(prep_dir),
                "--scale",
                str(scale),
                "--border",
                "30",
                "--only",
                name,
            ],
            capture_output=True,
        )
        if not prepped.exists():
            continue

        omr_dir = work / f"omr-{scale}"
        omr = run_audiveris(prepped, omr_dir)
        if omr is None:
            continue
        slots = rhythm_score(omr)
        print(f"  {name} at {scale}: {slots} slots")
        if best is None or slots > best[0]:
            best = (slots, omr)

    if best is None:
        print(f"{name}: Audiveris produced nothing")
        return 1

    slots, omr = best
    # Keep the winning scale's output under the ordinary names.
    final_prep = work / "prep"
    final_omr = work / "omr1"
    final_masked = work / "masked"
    for directory in (final_prep, final_omr, final_masked):
        directory.mkdir(parents=True, exist_ok=True)

    scale_dir = omr.parent.parent / f"omr-{omr.parent.name.split('-')[-1]}"
    prep_src = work / f"prep-{omr.parent.name.split('-')[-1]}" / f"{name}.png"
    shutil.copy(prep_src, final_prep / f"{name}.png")
    shutil.copy(omr, final_omr / f"{name}.omr")
    for extra in omr.parent.glob(f"{name}*"):
        if extra.suffix in (".log", ".mxl"):
            shutil.copy(extra, final_omr / extra.name)

    # The mask keeps the melody staff; it uses the .omr's staff geometry.
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "omr_mask.py"),
            str(final_omr / f"{name}.omr"),
            str(final_masked / f"{name}.png"),
        ],
        capture_output=True,
    )

    print(f"{name}: {slots} slots (scale {omr.parent.name.split('-')[-1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
