#!/usr/bin/env python3
"""Turn an Audiveris MusicXML export into the JSON that tools/vf/render.mjs wants.

Audiveris's own note reading is not trustworthy, but its *measure* reading is:
how many measures there are, where the time signatures change, and which
notes are rests.  So the export is used for the shape of the music, and the
notes are drawn in VexFlow so that the shape can be checked against the print.

Usage:
    mxl_to_vf.py <page.mxl> <out.json> [--part N]
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

# MusicXML <type> -> VexFlow duration
TYPE_TO_VEX = {
    "breve": "w",
    "whole": "w",
    "half": "h",
    "quarter": "q",
    "eighth": "8",
    "16th": "16",
    "32nd": "32",
    "64th": "32",
}

STEP_TO_VEX = {
    "C": "c", "D": "d", "E": "e", "F": "f", "G": "g", "A": "a", "B": "b",
}

# VexFlow's key signature names for the sharps/flats count.
KEY_NAMES = {
    (0, "major"): "C", (1, "major"): "G", (2, "major"): "D", (3, "major"): "A",
    (4, "major"): "E", (5, "major"): "B", (6, "major"): "F#", (7, "major"): "C#",
    (-1, "major"): "F", (-2, "major"): "Bb", (-3, "major"): "Eb",
    (-4, "major"): "Ab", (-5, "major"): "Db", (-6, "major"): "Gb",
    (-7, "major"): "Cb",
}


def read_mxl(path: Path) -> str:
    if path.suffix == ".mxl":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.endswith(".xml")]
            if not names:
                raise SystemExit(f"no xml inside {path}")
            return zf.read(names[0]).decode("utf-8", "replace")
    return path.read_text(encoding="utf-8", errors="replace")


def strip_ns(tag: str) -> str:
    return tag.split(":")[-1]


def parse_part(xml: str, part_index: int) -> tuple[list[dict], str, str]:
    """Return (measures, key signature, first time signature) for one part."""
    parts = re.findall(r"<part\b[^>]*>(.*?)</part>", xml, re.S)
    if not parts:
        raise SystemExit("no parts found")
    part = parts[min(part_index, len(parts) - 1)]

    key = ""
    time = ""
    measures: list[dict] = []

    for raw in re.findall(r"<measure\b[^>]*>(.*?)</measure>", part, re.S):
        # A time signature may change inside a measure.
        beats = None
        beat_type = None
        ts = re.search(r"<time\b[^>]*>(.*?)</time>", raw, re.S)
        if ts:
            b = re.search(r"<beats>(\d+)</beats>", ts.group(1))
            t = re.search(r"<beat-type>(\d+)</beat-type>", ts.group(1))
            if b and t:
                beats, beat_type = int(b.group(1)), int(t.group(1))
                if not time:
                    time = f"{beats}/{beat_type}"

        if not key:
            k = re.search(r"<key\b[^>]*>(.*?)</key>", raw, re.S)
            if k:
                fifths = re.search(r"<fifths>(-?\d+)</fifths>", k.group(1))
                if fifths:
                    key = KEY_NAMES.get((int(fifths.group(1)), "major"), "")

        notes = []
        for n in re.findall(r"<note\b[^>]*>(.*?)</note>", raw, re.S):
            if "<chord" in n[: n.find("</note>")]:
                continue  # a second note of a chord: keep the first only
            is_rest = "<rest" in n
            step = re.search(r"<step>(\w)</step>", n)
            octave = re.search(r"<octave>(-?\d+)</octave>", n)
            alter = re.search(r"<alter>(-?\d+)</alter>", n)
            vtype = re.search(r"<type>(\w+)</type>", n)
            dot = n.count("<dot")
            chord = "<chord" in n

            duration = TYPE_TO_VEX.get(vtype.group(1), "q") if vtype else "q"
            if is_rest:
                duration += "r"

            if is_rest or not step or not octave:
                vkey = "b/4"
            else:
                letter = STEP_TO_VEX.get(step.group(1), "c")
                acc = ""
                if alter:
                    a = int(alter.group(1))
                    acc = "#" * a if a > 0 else "b" * (-a)
                vkey = f"{letter}{acc}/{octave.group(1)}"

            notes.append(
                {
                    "key": vkey,
                    "duration": duration,
                    "dotted": bool(dot),
                    "rest": bool(is_rest),
                    "chord": chord,
                }
            )

        measures.append(
            {
                "beats": beats if beats else 4,
                "beatType": beat_type if beat_type else 4,
                "notes": notes,
            }
        )

    return measures, key, time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mxl")
    ap.add_argument("out")
    ap.add_argument("--part", type=int, default=0)
    ap.add_argument("--systems", type=int, default=0, help="split measures into systems of this many bars")
    args = ap.parse_args()

    xml = read_mxl(Path(args.mxl))
    measures, key, time = parse_part(xml, args.part)

    systems = []
    if args.systems and args.systems > 0:
        for i in range(0, len(measures), args.systems):
            systems.append({"measures": measures[i : i + args.systems]})
    else:
        systems.append({"measures": measures})

    doc = {"title": Path(args.mxl).stem, "key": key, "time": time, "systems": systems}
    Path(args.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"{args.out}  key={key or '-'} time={time or '-'} measures={len(measures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
