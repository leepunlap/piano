#!/usr/bin/env python3
"""Turn a song file into the JSON that tools/vf/render.mjs and web/vf.js read.

The song file knows where every note is on the page; what it does not know is
which bar each note belongs to, and the bars are what notation is written in.
The bar lines are known, so the notes are split at them.

Usage:
    song_to_vf.py <song.json> <out.json> [--part all]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

LETTER_TO_VEX = {"C": "c", "D": "d", "E": "e", "F": "f", "G": "g", "A": "a", "B": "b"}

# The note length, as VexFlow wants it.
DURATION = {
    "whole": "w",
    "half": "h",
    "quarter": "q",
    "eighth": "8",
    "16th": "16",
    "32nd": "32",
}


def note_key(note: dict) -> str:
    letter = LETTER_TO_VEX.get(note.get("pitch", "C"), "c")
    alter = note.get("alter", 0)
    accidental = "#" * alter if alter > 0 else "b" * (-alter)
    return f"{letter}{accidental}/{note.get('octave', 4)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("song")
    ap.add_argument("out")
    args = ap.parse_args()

    song = json.loads(Path(args.song).read_text(encoding="utf-8"))

    systems = []
    for system in song.get("systems", []):
        notes = system.get("notes", [])
        barlines = sorted(system.get("barlines", []))
        if not notes:
            continue

        # The bar lines split the system into bars; the first bar starts at
        # the first note.
        edges = [notes[0]["x"] - 1] + [b for b in barlines if b > notes[0]["x"]]
        edges.append(notes[-1]["x"] + 1)

        measures = []
        for start, end in zip(edges, edges[1:]):
            inside = [n for n in notes if start <= n["x"] < end]
            if not inside:
                continue
            # A bar's length in beats: from the first note of the bar to the
            # end of the last one.  (Taking it from the next bar's first note
            # would give the length of everything between them, which is the
            # whole system when the bar lines are not being used.)
            first = inside[0]
            last = inside[-1]
            beats = max(0.25, (last["beat"] + last.get("beats", 1.0)) - first["beat"])
            measures.append(
                {
                    "beats": round(beats, 4),
                    "notes": [
                        {
                            "key": note_key(n),
                            "duration": DURATION.get(n.get("duration", "quarter"), "q"),
                            "dotted": bool(n.get("dotted")),
                            "rest": bool(n.get("rest")),
                        }
                        for n in inside
                    ],
                }
            )

        systems.append({"measures": measures})

    doc = {
        "title": song.get("title", ""),
        "key": song.get("key", ""),
        "time": song.get("time", "4/4"),
        "systems": systems,
    }
    Path(args.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(
        f"{args.out}  {len(systems)} systems, "
        f"{sum(len(s['measures']) for s in systems)} bars"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
