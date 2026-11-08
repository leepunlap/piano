"""MIDI file and live-port I/O built on `mido`.

Everything the pipeline reads or writes goes through here so that the rest of
the code never has to care about MIDI channel, tempo or meta-message details.
"""

from __future__ import annotations

import os
from typing import Iterable, Iterator, Sequence as TSequence

import mido

from ..core.notes import Note, Sequence

DEFAULT_PPQ = 480
DEFAULT_TEMPO = 500000  # microseconds per beat -> 120 BPM


def _ppq(mid: mido.MidiFile) -> int:
    return mid.ticks_per_beat or DEFAULT_PPQ


def _tempo_map(mid: mido.MidiFile) -> list[tuple[float, float]]:
    """Build a (time_seconds, bpm) list from the file's tempo events.

    `mido` already converts delta times to seconds when parsing, so the
    accumulated time here is directly usable.
    """
    seconds = 0.0
    out: list[tuple[float, float]] = []

    for message in mid:
        seconds += message.time
        if message.type == "set_tempo":
            out.append((seconds, 60_000_000.0 / message.tempo))

    if not out:
        out.append((0.0, 60_000_000.0 / DEFAULT_TEMPO))
    return out


def read_midi(path: str, name: str = "") -> Sequence:
    """Read a MIDI file into a `Sequence`.

    Notes are matched on-pitch: an `on` creates a note, the matching `off`
    (same pitch, any channel) closes it.  Unclosed notes are given a default
    length so that a malformed take still yields usable data.

    Note that `mido` reports `message.time` in seconds already.
    """
    mid = mido.MidiFile(path)

    pending: dict[tuple[int, int], tuple[float, int]] = {}
    notes: list[Note] = []
    seconds = 0.0

    for message in mid:
        seconds += message.time

        if message.type == "note_on" and message.velocity > 0:
            pending[(message.channel, message.note)] = (seconds, message.velocity)
        elif message.type in ("note_off", "note_on"):
            key = (message.channel, message.note)
            opened = pending.pop(key, None)
            if opened is not None:
                start, velocity = opened
                notes.append(Note(message.note, start, seconds, velocity))

    # Close any notes left hanging.
    for (_, pitch), (start, velocity) in pending.items():
        notes.append(Note(pitch, start, start + 0.25, velocity))

    return Sequence(
        notes=notes,
        tempo_map=_tempo_map(mid),
        name=name or os.path.splitext(os.path.basename(path))[0],
    )


class _TickWriter:
    """Converts seconds to MIDI ticks against a tempo map.

    `mido` reads delta times back as seconds but requires *tick* deltas when
    saving, so the conversion has to be done explicitly here.
    """

    def __init__(self, tempo_map: Sequence[tuple[float, float]], ppq: int):
        self.ppq = ppq
        self.tempi = sorted(tempo_map) or [(0.0, 120.0)]

    def to_tick(self, time_s: float) -> int:
        beats = 0.0
        current_time = 0.0
        current_bpm = self.tempi[0][1]
        for change_time, change_bpm in self.tempi:
            if change_time <= time_s:
                if change_time > current_time:
                    beats += (change_time - current_time) * current_bpm / 60.0
                current_time = change_time
                current_bpm = change_bpm
            else:
                break
        beats += (time_s - current_time) * current_bpm / 60.0
        return int(round(beats * self.ppq))


def _write(sequence: Sequence, path: str, ppq: int, use_tempo_map: bool) -> str:
    """Shared MIDI writing implementation."""
    mid = mido.MidiFile(ticks_per_beat=ppq)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    tempi = sorted(sequence.tempo_map) or [(0.0, 120.0)]
    if not use_tempo_map:
        tempi = [tempi[0]]

    writer = _TickWriter(tempi, ppq)

    events: list[tuple[float, int, object]] = []
    for time_s, change_bpm in tempi:
        events.append(
            (time_s, -1, mido.MetaMessage("set_tempo", tempo=int(round(60_000_000.0 / change_bpm)), time=0))
        )
    for index, note in enumerate(sequence.sorted_notes()):
        velocity = max(1, min(127, int(note.velocity)))
        events.append((note.start, index, mido.Message("note_on", note=note.pitch, velocity=velocity, time=0)))
        events.append((note.end, index, mido.Message("note_off", note=note.pitch, velocity=0, time=0)))

    events.sort(key=lambda e: (e[0], e[1]))

    prev_tick = 0
    for time_s, _, message in events:
        tick = writer.to_tick(time_s)
        delta = max(0, tick - prev_tick)
        prev_tick = tick
        track.append(message.copy(time=delta))

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    mid.save(path)
    return path


def write_midi(sequence: Sequence, path: str, ppq: int = DEFAULT_PPQ) -> str:
    """Write a `Sequence` to a standard MIDI file (single tempo)."""
    return _write(sequence, path, ppq, use_tempo_map=False)


def write_midi_with_tempo_map(sequence: Sequence, path: str, ppq: int = DEFAULT_PPQ) -> str:
    """Write a MIDI file that honours the full tempo map (tempo changes)."""
    return _write(sequence, path, ppq, use_tempo_map=True)


def list_midi_inputs() -> list[str]:
    """Names of available MIDI input ports."""
    try:
        return sorted(mido.get_input_names())
    except Exception:
        return []


def open_input(port: str | None = None) -> mido.BaseInput:
    """Open a MIDI input, returning a queued reader."""
    return mido.open_input(port)
