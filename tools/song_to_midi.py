#!/usr/bin/env python3
"""Turn a recognised song into MIDI, and the MIDI into sound.

The song file holds the melody as it was read off the page.  This turns it
into a performance: the melody as written, and the chords underneath it.

The chords are the arrangement's own — the ones printed above the staff — and
they are kept exactly as printed.  That matters, because the whole point of
the practice system is that it may change the tempo and the style of what is
played and must not quietly change the harmony.

Usage:
    song_to_midi.py song.json out.mid [--chords chords.json] [--bpm 84]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from build_song import place_chords
from piano.core.notes import Note, Sequence
from piano.io.midi_io import write_midi

STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Chord spellings, in semitones from the root.
CHORD_SHAPES = {
    "": [0, 4, 7],
    "m": [0, 3, 7],
    "7": [0, 4, 7, 10],
    "m7": [0, 3, 7, 10],
    "maj7": [0, 4, 7, 11],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "sus4": [0, 5, 7],
    "sus2": [0, 2, 7],
    "6": [0, 4, 7, 9],
    "m6": [0, 3, 7, 9],
    "9": [0, 4, 7, 10, 14],
    "add9": [0, 4, 7, 14],
    "5": [0, 7],
}


def parse_chord(text: str) -> tuple[int, list[int]] | None:
    """Turn 'C#m/A#' into a root pitch and the notes of the chord."""
    if not text:
        return None
    root = text[0].upper()
    if len(text) > 1 and text[1] in "#b":
        root += text[1]
        rest = text[2:]
    else:
        rest = text[1:]
    if root[0] not in STEP_SEMITONE:
        return None
    accidental = 0
    if len(root) > 1:
        accidental = 1 if root[1] == "#" else -1
    root_pitch = STEP_SEMITONE[root[0]] + accidental
    # The part after the slash is a bass note.
    bass = None
    if "/" in rest:
        rest, bass_text = rest.split("/", 1)
        bass = bass_text.strip()
    shape = None
    for name in sorted(CHORD_SHAPES, key=len, reverse=True):
        if rest.startswith(name):
            shape = CHORD_SHAPES[name]
            break
    if shape is None:
        return None
    notes = [root_pitch + s for s in shape]
    if bass:
        b = bass[0].upper()
        acc = 0
        if len(bass) > 1 and bass[1] in "#b":
            acc = 1 if bass[1] == "#" else -1
        if b in STEP_SEMITONE:
            bass_pitch = STEP_SEMITONE[b] + acc
            notes = [bass_pitch - 12] + notes
    return root_pitch, notes


def melody_events(song: dict) -> list[tuple[float, float, int, int]]:
    """(start in beats, length in beats, midi, velocity)."""
    out = []
    for system in song.get("systems", []):
        for note in system.get("notes", []):
            if note.get("rest"):
                continue
            midi = (note["octave"] + 1) * 12 + STEP_SEMITONE[note["pitch"]] + note.get("alter", 0)
            out.append(
                (
                    note.get("beat", 0.0),
                    max(0.05, note.get("beats", 1.0)),
                    midi,
                    96,
                )
            )
    out.sort(key=lambda e: e[0])
    return out


def chord_events(song: dict, chords: dict) -> list[tuple[float, float, list[int]]]:
    """(start in beats, length in beats, midi notes)."""
    out = []
    for system in song.get("systems", []):
        key = f"{system['page']}:{system['system']}"
        for entry in chords.get(key, []):
            parsed = parse_chord(entry["chord"])
            if not parsed:
                continue
            root, notes = parsed
            out.append((entry["beat"], entry["beats"], notes))
    out.sort(key=lambda e: e[0])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("song", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--chords", type=Path)
    ap.add_argument(
        "--bpm",
        type=float,
        default=None,
        help="the tempo; by default the one printed on the score",
    )
    args = ap.parse_args()

    song = json.loads(args.song.read_text(encoding="utf-8"))
    # The song file normally carries its own chord symbols, already placed.
    chords = song.get("chords", {})
    if args.chords and args.chords.exists():
        raw = json.loads(args.chords.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            chords = raw
        else:
            # A flat list of symbols: place them with the song's own bars.
            chords = place_chords(song, raw)

    if args.bpm is None:
        args.bpm = float(song.get("bpm", 84.0))

    melody = melody_events(song)
    comps = chord_events(song, chords)

    events: list[tuple[float, float, int, int]] = list(melody)
    for start, length, notes in comps:
        # A chord sounds as a short arpeggio rather than one block, which is
        # how a chord is played on a keyboard and how it reads as an
        # accompaniment rather than as a second melody.
        step = min(0.25, length / 4.0)
        for i, pitch in enumerate(notes):
            events.append((start + i * step * 0.5, step * 1.6, pitch - 12, 62))

    events.sort(key=lambda e: e[0])
    seconds_per_beat = 60.0 / args.bpm
    sequence = Sequence(name=song.get("title", "score"))
    sequence.tempo_map = [(0.0, args.bpm)]
    for start, length, pitch, velocity in events:
        sequence.add(
            Note(
                pitch=max(0, min(127, int(pitch))),
                start=start * seconds_per_beat,
                end=(start + length) * seconds_per_beat,
                velocity=velocity,
            )
        )
    write_midi(sequence, str(args.out))
    print(
        f"{args.out}: {len(melody)} melody notes, {len(comps)} chords, "
        f"{args.bpm:g} bpm"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
