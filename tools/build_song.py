#!/usr/bin/env python3
"""Build one song from its scanned pages.

The output is a JSON file with everything the web page needs:

  * the printed music, as one image per system, cleaned up — deskewed, with
    the paper flattened and the contrast pulled — so that the score and its
    lyrics can be read on a screen instead of off a photograph;
  * the chord symbols, with where on the staff they sit, so that the practice
    system can keep the chords whatever else it does to the melody;
  * the melody, as notes with a pitch, a place in the bar and a length, so
    that the piece can be played and compared against.

Usage:
    build_song.py <song> --pages work/omr/pages/besame --out work/songs/besame.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from omr_bands import read_omr
from omr_sheet import read_page as read_sheet
from read_page import read_page

# Audiveris names a note's length the way a font does; the song file names
# it the way a musician does.
DURATION_NAMES = {
    "w": "whole",
    "h": "half",
    "q": "quarter",
    "8": "eighth",
    "16": "16th",
    "32": "32nd",
}


def flatten(img: Image.Image) -> Image.Image:
    """Make the paper white and the ink black.

    The paper of a printed page is not white, and it is not the same shade
    everywhere either, so the paper level is estimated from the page itself
    and everything darker than it becomes ink.
    """
    gray = np.asarray(img.convert("L")).astype(np.float64)
    paper = float(np.percentile(gray, 70))
    ink = gray < paper * 0.72
    out = np.where(ink, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def system_strips(img: Image.Image, staves, systems) -> list[dict]:
    """One image per system: the music as printed, cleaned up.

    The strip is cut round the melody staff, but generously: the chord
    symbols sit above it and the lyrics below it, and both are wanted on the
    page.  The bass staff of a piano system may creep in at the bottom, which
    is the lesser fault.
    """
    by_id = {s.sid: s for s in staves}
    out: list[dict] = []
    for sys_id, ids in sorted(systems.items()):
        ids = [i for i in ids if i in by_id]
        if not ids:
            continue
        top = min(ids, key=lambda i: by_id[i].line_y(0, by_id[i].left))
        first, last = by_id[top], by_id[top]
        x0 = max(0, int(first.left) - 12)
        x1 = min(img.width, int(first.right) + 12)
        inter = first.interline
        y0 = max(0, int(first.line_y(0, first.left) - inter * 7.0))
        y1 = min(img.height, int(last.line_y(4, last.right) + inter * 4.0))
        out.append(
            {
                "system": sys_id,
                "box": [x0, y0, x1, y1],
                "staves": ids,
                "melody_staff": top,
            }
        )
    return out


def measure_notes(notes, barlines, staff, beats_per_measure: float, bar_offset: int = 0):
    """Sort notes into bars and give each one its place in the song.

    The place is worked out from where the note sits on the page rather than
    from how long the reader thinks it is.  Printed music is spaced in
    proportion to time, so the distance from the start of the bar says how far
    into the bar a note falls — and that is a much steadier measurement than
    counting flags.

    `bar_offset` is how many bars came before this system, so that the beats
    run on from one system to the next instead of restarting.
    """
    if not notes:
        return [], bar_offset
    # The bars of this system are the stretches between the bar lines.
    bounds = sorted(b for b in barlines if b > notes[0].x - 1)
    starts = [notes[0].x] + bounds
    ends = bounds + [notes[-1].x + 1.0]
    bars = [
        {"start": s, "end": e, "notes": []}
        for s, e in zip(starts, ends)
        if e > s
    ]
    for note in notes:
        for bar in bars:
            if bar["start"] <= note.x < bar["end"]:
                bar["notes"].append(note)
                break
    for index, bar in enumerate(bars):
        span = max(1.0, bar["end"] - bar["start"])
        for note in bar["notes"]:
            note.beat = (bar_offset + index) * beats_per_measure + (
                (note.x - bar["start"]) / span * beats_per_measure
            )
    return bars, bar_offset + len(bars)


def place_chords(song: dict, chords: list[dict]) -> dict:
    """Give every chord symbol its place in the song.

    A chord symbol is printed above the music, at the point where the
    harmony changes, so its place is found from the notes: the symbol belongs
    to the first note at or after it.  A symbol printed over a bar of rests
    therefore lands at the start of that bar, which is where it belongs.

    The symbol's length runs to the next symbol, so that the harmony changes
    when the music says it does and not when the next bar happens to start.
    """
    by_system: dict[tuple[str, int], list[dict]] = {}
    for chord in chords:
        by_system.setdefault((chord["page"], int(chord["system"])), []).append(chord)

    out: dict[str, list[dict]] = {}
    for system in song.get("systems", []):
        key = (system["page"], int(system["system"]))
        items = by_system.get(key)
        notes = system.get("notes", [])
        if not items or not notes:
            continue
        events = sorted(notes, key=lambda n: n["x"])

        placed = []
        for chord in sorted(items, key=lambda c: c["x"]):
            x = chord["x"]
            after = [n for n in events if n["x"] >= x - 2]
            if after:
                beat = after[0].get("beat", 0.0)
            else:
                beat = max(0.0, events[-1].get("beat", 0.0))
            placed.append({"chord": chord["chord"], "beat": round(beat, 3), "end": None})

        # Each symbol lasts until the next one.
        for index, entry in enumerate(placed):
            if index + 1 < len(placed):
                entry["end"] = placed[index + 1]["beat"]
            else:
                entry["end"] = None

        entry_list = []
        for index, entry in enumerate(placed):
            start = entry["beat"]
            end = entry["end"]
            if end is None:
                following = [n for n in events if n.get("beat", 0.0) > start]
                end = following[0].get("beat", start + 4.0) if following else start + 4.0
            length = max(0.25, end - start)
            entry_list.append({"chord": entry["chord"], "beat": start, "beats": round(length, 3)})
        out[f"{system['page']}:{system['system']}"] = entry_list
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("song")
    ap.add_argument("--pages", required=True, type=Path)
    ap.add_argument("--prep", required=True, type=Path)
    ap.add_argument("--omr", required=True, type=Path)
    ap.add_argument("--masked", required=True, type=Path)
    ap.add_argument("--strips", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--clef", default="treble")
    ap.add_argument("--beats", type=float, default=4.0)
    ap.add_argument("--bpm", type=float, default=84.0, help="the tempo printed on the score")
    ap.add_argument("--chords", type=Path, help="JSON list of chord symbols to place")
    args = ap.parse_args()

    args.strips.mkdir(parents=True, exist_ok=True)
    pages = sorted(args.pages.glob("*.png"))
    song = {
        "title": args.song,
        "clef": args.clef,
        "bpm": args.bpm,
        "pages": [],
        "systems": [],
    }

    # One of the scores was scanned with a page twice: the photograph of
    # 沉默是金's first page was inserted for both pages 54 and 55.  Reading
    # the same page twice would put the same bars in the song twice, so the
    # repeat is dropped.
    seen: dict[str, str] = {}
    page_offset = 0.0

    for page in pages:
        name = page.stem
        digest = hashlib.md5(page.read_bytes()).hexdigest()
        if digest in seen:
            print(f"skipping {name}: same page as {seen[digest]}")
            continue
        seen[digest] = name
        omr_path = args.omr / f"{name}.omr"
        masked_path = args.masked / f"{name}.png"
        if not omr_path.exists() or not masked_path.exists():
            print(f"skipping {name}: not processed")
            continue

        staves, systems = read_omr(omr_path)
        prepped = Image.open(args.prep / f"{name}.png").convert("L")
        masked = Image.open(masked_path)

        # The strip for display: the printed music, as it stands.
        strips = system_strips(prepped, staves, systems)
        for entry in strips:
            box = entry["box"]
            strip = prepped.crop(tuple(box))
            # The paper is flattened to white and the ink to black, so the
            # score reads on a screen the way it reads on paper.
            strip = flatten(strip)
            strip_path = args.strips / f"{name}-sys{entry['system']:02d}.png"
            strip.save(strip_path)
            entry["image"] = strip_path.name

        # The melody, read off the cleaned-up page.
        #
        # The reading comes from Audiveris's own analysis, kept in the sheet
        # XML inside the .omr file.  That analysis knows where every bar
        # line, note head, rest and beam is, and in what order, and it knows
        # the rhythm exactly; what it does not know is the note *name*, which
        # is worked out here from the clef and the key signature.
        read = read_sheet(omr_path)
        # The beats run on from one page to the next, so that the whole
        # piece is one continuous timeline.
        for entry in read:
            for n in entry["notes"]:
                n["beat"] = n["beat"] + page_offset
            strip_entry = next(s for s in strips if s["system"] == entry["system"])
            # The beat of a note is its place in the bar, measured in whole
            # notes by Audiveris, turned into quarter-note beats and run on
            # from one system to the next.
            notes = []
            for n in entry["notes"]:
                notes.append(
                    {
                        "x": n["x"],
                        "beat": round(n["beat"], 4),
                        "pitch": n["pitch"],
                        "octave": n["octave"],
                        "diatonic": n["diatonic"],
                        "alter": n["alter"],
                        "duration": DURATION_NAMES.get(n["duration"], n["duration"]),
                        "beats": round(n["beats"], 4),
                        "dotted": n["dotted"],
                        "rest": n["rest"],
                        "stem": 0,
                        "beams": 0,
                    }
                )
            song["systems"].append(
                {
                    "system": entry["system"],
                    "page": name,
                    "image": strip_entry["image"],
                    "box": strip_entry["box"],
                    "notes": notes,
                    "barlines": entry["barlines"],
                }
            )
            song["pages"].append({"page": name, "system": entry["system"]})
        if read and read[-1]["notes"]:
            page_offset = max(n["beat"] + n["beats"] for n in read[-1]["notes"])
        print(f"{name}: {len(read)} systems, {sum(len(e['notes']) for e in read)} notes")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    song["chords"] = {}
    if args.chords and args.chords.exists():
        raw = json.loads(args.chords.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = [c for items in raw.values() for c in items]
        song["chords"] = place_chords(song, raw)
        print(f"placed {sum(len(v) for v in song['chords'].values())} chord symbols")
    args.out.write_text(json.dumps(song, indent=1), encoding="utf-8")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
