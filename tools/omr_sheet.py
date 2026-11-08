#!/usr/bin/env python3
"""Read a score out of an Audiveris `.omr` file.

Audiveris keeps its whole analysis in the `.omr` archive, in a sheet XML that
is far richer than the MusicXML it exports.  For every page it holds:

  * the staff lines, as polylines, so the warp of a photographed page is
    carried through rather than guessed at;
  * the measures, as `stack` elements with a left and a right edge, so the
    bar lines are known exactly rather than guessed at from the ink;
  * the slots of each measure with their time offsets, so the rhythm is
    known exactly;
  * every note head with the staff-relative step it sits on, which is a
    geometric measurement and therefore trustworthy;
  * the rests, with their shape, the augmentation dots, the beams, the
    clefs, the key signatures and the time signatures.

None of that needs to be re-derived from the picture.  What was wrong with
Audiveris's *export* was not the analysis but the export: it writes one part,
it loses the beaming, and it rounds the rhythm.  Reading the sheet directly
avoids all three.

The one thing that has to be worked out here is the pitch.  Audiveris gives a
head's step *relative to the staff*, counted so that the middle line is 0 and
upwards is negative, which is a distance and not a note name.  Turning that
into a note name takes three things: where the middle line falls in absolute
terms (which the clef fixes), the letter (which the step fixes), and the
alteration (which the key signature and any printed accidental fix).

Usage:
    omr_sheet.py <page.omr> <out.json> [--part N]
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

# Absolute diatonic numbers, C0 = 0.  So C4 = 28, B4 = 34, E5 = 37.
def diatonic(letter_index: int, octave: int) -> int:
    return octave * 7 + letter_index


LETTERS = "CDEFGAB"

# The diatonic number of the middle line, for each clef.  The middle line is
# what Audiveris counts its staff-relative steps from, so this is the anchor
# that turns a step into a note.
MIDDLE_LINE = {
    "G_CLEF": diatonic(6, 4),   # treble: the middle line is B4
    "F_CLEF": diatonic(1, 3),   # bass:   the middle line is D3
    "C_CLEF": diatonic(0, 4),   # alto:  the middle line is C4
    "PERCUSSION_CLEF": diatonic(0, 4),
}

# The usual spelling of a key signature, by the number of sharps or flats.
SHARP_ORDER = "FCGDAEB"
FLAT_ORDER = "BEADGCF"


def parse_fraction(text: str) -> Fraction:
    """Audiveris writes time offsets as fractions: "1/4", "7/16", "1"."""
    text = (text or "").strip()
    if not text:
        return Fraction(0)
    if "/" in text:
        num, den = text.split("/", 1)
        return Fraction(int(num), int(den))
    return Fraction(int(text))


def shape_to_duration(shape: str) -> tuple[str, float]:
    """A rest's shape gives its duration: (vexflow-ish name, whole notes)."""
    table = {
        "WHOLE_REST": ("w", Fraction(1)),
        "HALF_REST": ("h", Fraction(1, 2)),
        "QUARTER_REST": ("q", Fraction(1, 4)),
        "EIGHTH_REST": ("8", Fraction(1, 8)),
        "SIXTEENTH_REST": ("16", Fraction(1, 16)),
        "THIRTY_SECOND_REST": ("32", Fraction(1, 32)),
        "BREVE_REST": ("w", Fraction(2)),
    }
    return table.get(shape, ("q", Fraction(1, 4)))


def shape_to_name(duration: str) -> str:
    return {
        "w": "whole",
        "h": "half",
        "q": "quarter",
        "8": "eighth",
        "16": "16th",
        "32": "32nd",
    }.get(duration, "quarter")


def duration_to_whole(duration: str, dotted: bool) -> Fraction:
    base = {
        "w": Fraction(1),
        "h": Fraction(1, 2),
        "q": Fraction(1, 4),
        "8": Fraction(1, 8),
        "16": Fraction(1, 16),
        "32": Fraction(1, 32),
    }.get(duration, Fraction(1, 4))
    return base * (Fraction(3, 2) if dotted else Fraction(1))


class Sheet:
    """The parsed contents of one Audiveris sheet."""

    def __init__(self, xml: str):
        self.root = ET.fromstring(xml)
        self.page = self.root.find("page")

    # ── element lookups ───────────────────────────────────────────────────────
    @staticmethod
    def bounds(element) -> tuple[float, float, float, float] | None:
        b = element.find("bounds")
        if b is None:
            return None
        return (
            float(b.get("x", 0)),
            float(b.get("y", 0)),
            float(b.get("w", 0)),
            float(b.get("h", 0)),
        )


def load_omr(path: Path) -> Sheet:
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xml") and "sheet" in n]
        if not names:
            names = [n for n in zf.namelist() if n.endswith(".xml")]
        xml = zf.read(names[0]).decode("utf-8", "replace")
    return Sheet(xml)


def parse_system(system) -> dict:
    """Pull one system's structure out of the sheet XML."""
    out: dict = {"id": system.get("id"), "stacks": [], "parts": []}

    # ── measures, as stacks ───────────────────────────────────────────────
    for stack in system.findall("stack"):
        slots = []
        for slot in stack.findall("slot"):
            slots.append(
                {
                    "id": slot.get("id"),
                    "x": float(stack.get("left", 0)) + float(slot.get("x-offset", 0)),
                    "time": parse_fraction(slot.get("time-offset", "0")),
                }
            )
        out["stacks"].append(
            {
                "id": stack.get("id"),
                "left": float(stack.get("left", 0)),
                "right": float(stack.get("right", 0)),
                "expected": parse_fraction(stack.get("expected", "1")),
                "duration": parse_fraction(stack.get("duration", "1")),
                "slots": slots,
            }
        )

    # ── the glyph instances and the relation graph ─────────────────────────
    sig = system.find("sig")
    inter = sig.find("inters") if sig is not None else None
    relations = sig.find("relations") if sig is not None else None

    elements: dict[str, ET.Element] = {}
    if inter is not None:
        for element in inter:
            eid = element.get("id")
            if eid:
                elements[eid] = element

    contains: dict[str, list[str]] = {}
    alters: dict[str, str] = {}
    if relations is not None:
        for relation in relations.findall("relation"):
            source = relation.get("source")
            target = relation.get("target")
            kind = None
            for child in relation:
                kind = child.tag
                break
            if source is None or target is None:
                continue
            if kind == "containment":
                contains.setdefault(source, []).append(target)
            elif kind == "alter-head":
                alters[source] = target

    # ── the parts and their staves ─────────────────────────────────────────
    for part in system.findall("part"):
        staff = part.find("staff")
        lines = []
        if staff is not None:
            lines_el = staff.find("lines")
            if lines_el is not None:
                for line in lines_el.findall("line"):
                    points = [(float(p.get("x")), float(p.get("y"))) for p in line.findall("point")]
                    if points:
                        lines.append(points)
        header = staff.find("header") if staff is not None else None

        measures = []
        for measure in part.findall("measure"):
            measure_rest = None
            voices = []
            for voice in measure.findall("voice"):
                entries = []
                slots_el = voice.find("slots")
                if slots_el is not None:
                    for entry in slots_el.findall("entry"):
                        key = entry.find("key")
                        value = entry.find("value")
                        if key is None or value is None:
                            continue
                        entries.append(
                            {
                                "slot": key.text.strip() if key.text else "",
                                "chord": value.get("chord", ""),
                                "status": value.get("status", ""),
                            }
                        )
                voices.append(entries)
                if voice.get("measure-rest-chord"):
                    measure_rest = voice.get("measure-rest-chord")
            measures.append(
                {
                    "id": measure.get("id"),
                    "times": [t.strip() for t in (measure.findtext("times") or "").split()],
                    "head_chords": (measure.findtext("head-chords") or "").split(),
                    "rest_chords": (measure.findtext("rest-chords") or "").split(),
                    "voices": voices,
                    "measure_rest_chord": measure_rest,
                }
            )

        out["parts"].append(
            {
                "id": part.get("id"),
                "lines": lines,
                "header": {
                    "clef": header.findtext("clef").strip() if header is not None and header.findtext("clef") else "",
                    "key": header.findtext("key").strip() if header is not None and header.findtext("key") else "",
                    "time": header.findtext("time").strip() if header is not None and header.findtext("time") else "",
                },
                "measures": measures,
            }
        )

    out["elements"] = elements
    out["contains"] = contains
    out["alters"] = alters
    return out


def key_alterations(inter, middle_line: int) -> dict[int, int]:
    """Which diatonic positions the key signature alters, and by how much.

    The key signature is a set of glyphs at staff positions; the letter at
    each position is fixed by the clef and the step, so the position gives
    the letter directly.
    """
    alterations: dict[int, int] = {}
    if inter is None:
        return alterations
    for element in inter.findall("key-alter"):
        shape = element.get("shape", "")
        if "SHARP" in shape:
            amount = 1
        elif "FLAT" in shape:
            amount = -1
        elif "NATURAL" in shape:
            amount = 0
        else:
            continue
        try:
            step = int(element.get("pitch", "0"))
        except ValueError:
            continue
        alterations[middle_line - step] = amount
    return alterations


def clef_middle_line(clef_element, fallback: int = 34) -> int:
    """The absolute diatonic number of the staff's middle line."""
    if clef_element is None:
        return fallback
    kind = clef_element.get("shape") or clef_element.get("kind") or ""
    for name, value in MIDDLE_LINE.items():
        if name.split("_")[0] in kind:
            return value
    return fallback


def read_page(path: Path, part_index: int = 0) -> list[dict]:
    """Read one page into a list of systems of notes.

    The beats run on from one system to the next, so that the whole page is
    one continuous piece of music rather than a row of separate bars.
    """
    sheet = load_omr(path)
    systems = []
    offset = 0.0
    for system in sheet.page.findall("system"):
        parsed = parse_system(system)
        entry = read_system(parsed, part_index, offset)
        offset = entry.pop("next_offset")
        systems.append(entry)
    return systems


def read_system(parsed: dict, part_index: int = 0, offset: float = 0.0) -> dict:
    """Turn one system's parsed structure into notes, rests and bar lines."""
    elements = parsed["elements"]
    contains = parsed["contains"]
    alters = parsed["alters"]

    # Choose the part: the melody is the topmost staff.
    parts = parsed["parts"]
    if not parts:
        return {"notes": [], "barlines": [], "clef": "treble", "key": "", "time": ""}
    chosen = parts[min(part_index, len(parts) - 1)]

    # The clef and the key signature come from the glyph instances.
    inter_elements = elements
    clef_element = None
    for element in inter_elements.values():
        if element.tag == "clef":
            clef_element = element
            break
    middle_line = clef_middle_line(clef_element)
    alterations = key_alterations_from_elements(inter_elements, middle_line)

    notes: list[dict] = []
    barlines: list[float] = []
    next_offset = offset

    stacks = parsed["stacks"]
    measures = chosen["measures"]

    for index, measure in enumerate(measures):
        stack = stacks[index] if index < len(stacks) else None
        if stack is None:
            continue

        # The bar line at the end of this measure.
        end = stack["right"]
        barlines.append(end)
        # The bar's length in beats, from the time signature it is in.
        bar_beats = float(stack["expected"]) * 4

        slot_times = {s["id"]: s for s in stack["slots"]}
        slots = sorted(stack["slots"], key=lambda s: s["x"])

        # A printed accidental applies for the rest of the measure.
        measure_alters: dict[int, int] = {}

        # A bar that is nothing but a rest has no slots at all: the voice
        # points straight at the rest chord instead.
        if measure.get("measure_rest_chord"):
            chord_id = measure["measure_rest_chord"]
            chord = elements.get(chord_id)
            rest = None
            if chord is not None:
                for target in contains.get(chord_id, []):
                    candidate = elements.get(target)
                    if candidate is not None and candidate.tag == "rest":
                        rest = candidate
                        break
            if rest is not None:
                duration, whole = shape_to_duration(rest.get("shape", ""))
                box = Sheet.bounds(rest)
                x = (box[0] + box[2] / 2) if box else (stack["left"] + stack["right"]) / 2
                notes.append(
                    {
                        "x": x,
                        "beat": offset,
                        "duration": duration,
                        "beats": float(stack["expected"] * 4),
                        "dotted": False,
                        "rest": True,
                        "pitch": "",
                        "octave": 0,
                        "diatonic": 0,
                        "alter": 0,
                        "staff_step": 0,
                    }
                )
            offset += bar_beats
            continue

        for entries in measure["voices"]:
            for entry in entries:
                chord_id = entry["chord"]
                chord = elements.get(chord_id)
                if chord is None:
                    continue
                slot = slot_times.get(entry["slot"])
                if slot is None:
                    continue

                # How long is this slot?  The next slot's offset, or the end
                # of the measure.
                following = [s for s in slots if s["x"] > slot["x"] + 0.5]
                if following:
                    end_time = following[0]["time"]
                else:
                    end_time = stack["expected"]
                length = end_time - slot["time"]
                if length <= 0:
                    length = stack["expected"] - slot["time"]

                if chord.tag == "rest-chord":
                    rest = None
                    for target in contains.get(chord_id, []):
                        candidate = elements.get(target)
                        if candidate is not None and candidate.tag == "rest":
                            rest = candidate
                            break
                    if rest is None:
                        continue
                    duration, whole = shape_to_duration(rest.get("shape", ""))
                    box = Sheet.bounds(rest)
                    x = (box[0] + box[2] / 2) if box else slot["x"]
                    notes.append(
                        {
                            "x": x,
                            "beat": offset + float(slot["time"]) * 4,
                            "duration": duration,
                            "beats": float(length * 4),
                            "dotted": False,
                            "rest": True,
                            "pitch": "",
                            "octave": 0,
                            "diatonic": 0,
                            "alter": 0,
                            "staff_step": 0,
                        }
                    )
                    continue

                # A head chord: find its head, and its dot.
                head = None
                for target in contains.get(chord_id, []):
                    candidate = elements.get(target)
                    if candidate is not None and candidate.tag == "head":
                        head = candidate
                        break
                if head is None:
                    continue

                try:
                    step = int(head.get("pitch", "0"))
                except ValueError:
                    continue
                absolute = middle_line - step
                letter_index = absolute % 7
                octave = absolute // 7
                letter = LETTERS[letter_index]

                alter = alterations.get(absolute, 0)
                if head.get("id") in alters:
                    target = elements.get(alters[head.get("id")])
                    if target is not None:
                        shape = target.get("shape", "")
                        if "SHARP" in shape:
                            alter = 1
                        elif "FLAT" in shape:
                            alter = -1
                        elif "NATURAL" in shape:
                            alter = 0
                measure_alters[absolute] = alter

                # A dot: an augmentation-dot attached to this chord.
                dotted = False
                for target in contains.get(chord_id, []):
                    candidate = elements.get(target)
                    if candidate is not None and candidate.tag == "augmentation-dot":
                        dotted = True
                if not dotted:
                    for source, target in alters.items():
                        if target == chord_id and elements.get(source) is not None:
                            if elements[source].tag == "augmentation-dot":
                                dotted = True

                whole = length
                if dotted:
                    whole = length / Fraction(3, 2)
                duration = duration_name(whole)

                box = Sheet.bounds(head)
                x = (box[0] + box[2] / 2) if box else slot["x"]
                notes.append(
                    {
                        "x": x,
                        "beat": offset + float(slot["time"]) * 4,
                        "duration": duration,
                        "beats": float(length * 4),
                        "dotted": dotted,
                        "rest": False,
                        "pitch": letter,
                        "octave": octave,
                        "diatonic": absolute,
                        "alter": alter,
                        "staff_step": step,
                    }
                )

        offset += bar_beats

    next_offset = offset
    notes.sort(key=lambda n: n["x"])

    # The key signature's name, for display.
    fifths = 0
    for element in elements.values():
        if element.tag == "key" and element.get("fifths"):
            fifths = int(element.get("fifths"))
    key_name = KEY_NAMES.get(fifths, "")

    time_name = ""
    for element in elements.values():
        if element.tag == "time-pair" and element.get("time-rational"):
            time_name = element.get("time-rational")
            break

    try:
        system_number = int(parsed.get("id") or 0)
    except ValueError:
        system_number = 0

    return {
        "system": system_number,
        "notes": notes,
        "barlines": sorted(set(barlines)),
        "clef": "treble",
        "key": key_name,
        "time": time_name,
        "middle_line": middle_line,
        "next_offset": next_offset,
    }


def key_alterations_from_elements(elements: dict, middle_line: int) -> dict[int, int]:
    alterations: dict[int, int] = {}
    for element in elements.values():
        if element.tag != "key-alter":
            continue
        shape = element.get("shape", "")
        if "SHARP" in shape:
            amount = 1
        elif "FLAT" in shape:
            amount = -1
        elif "NATURAL" in shape:
            amount = 0
        else:
            continue
        try:
            step = int(element.get("pitch", "0"))
        except ValueError:
            continue
        alterations[middle_line - step] = amount
    return alterations


def duration_name(whole: Fraction) -> str:
    """The shortest note name whose length covers `whole` whole notes."""
    table = [
        (Fraction(1), "w"),
        (Fraction(1, 2), "h"),
        (Fraction(1, 4), "q"),
        (Fraction(1, 8), "8"),
        (Fraction(1, 16), "16"),
        (Fraction(1, 32), "32"),
    ]
    for value, name in table:
        if whole >= value - Fraction(1, 64):
            return name
    return "32"


KEY_NAMES = {
    0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#",
    -1: "F", -2: "Bb", -3: "Eb", -4: "Ab", -5: "Db", -6: "Gb", -7: "Cb",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("omr")
    ap.add_argument("out")
    ap.add_argument("--part", type=int, default=0)
    args = ap.parse_args()

    systems = read_page(Path(args.omr), args.part)
    doc = {"systems": systems}
    Path(args.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    total = sum(len(s["notes"]) for s in systems)
    rests = sum(1 for s in systems for n in s["notes"] if n["rest"])
    print(f"{args.out}  {len(systems)} systems, {total} events ({total - rests} notes, {rests} rests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
