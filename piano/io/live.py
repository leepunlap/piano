"""Live MIDI monitoring and capture helpers.

Two things live here:

* `MidiMonitor` - a passive listener that reports every message, used to
  verify a keyboard is talking to the machine and to see what a device
  actually sends (many keyboards emit a flood of CC and program-change
  traffic alongside the notes).
* `MidiSession` - a two-way session: it can send the reference song to the
  keyboard (so the player hears it) while simultaneously recording what the
  player plays back.

The reference is *not* sent as raw MIDI to be recorded - it is played for
guidance only, and the recording is taken from the keyboard's input.  That
keeps the two streams clean.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import mido

from ..core.notes import Note, Sequence


@dataclass
class MonitorEvent:
    """One MIDI message, captured with its arrival time."""

    time: float
    message: mido.Message | mido.MetaMessage

    def describe(self) -> str:
        message = self.message
        if isinstance(message, mido.MetaMessage):
            return f"meta {message.type}"
        if message.type == "note_on":
            return f"note_on  ch{message.channel} pitch {message.note} vel {message.velocity}"
        if message.type == "note_off":
            return f"note_off ch{message.channel} pitch {message.note}"
        if message.type in ("control_change", "program_change"):
            return f"{message.type} ch{message.channel} {message.control if hasattr(message, 'control') else message.program}"
        return f"{message.type} ch{message.channel}"


class MidiMonitor:
    """Listens on a MIDI input and collects every message.

    Used both as a diagnostic (`python3 -m piano.cli monitor`) and as the
    capture mechanism underneath `MidiSession`.
    """

    def __init__(self, port: str | None = None, include_meta: bool = False) -> None:
        self.port = port
        self.include_meta = include_meta
        self.events: list[MonitorEvent] = []
        self._start = 0.0
        self._input: Optional[mido.BaseInput] = None

    def _callback(self, message: mido.Message | mido.MetaMessage) -> None:
        if isinstance(message, mido.MetaMessage) and not self.include_meta:
            return
        self.events.append(MonitorEvent(time.monotonic() - self._start, message))

    def __enter__(self) -> "MidiMonitor":
        self._input = mido.open_input(self.port)
        self._start = time.monotonic()
        self._input.callback = self._callback
        return self

    def __exit__(self, *exc) -> None:
        if self._input is not None:
            self._input.close()
            self._input = None

    def notes(self) -> list[Note]:
        """The note events seen so far, as a `Sequence`."""
        pending: dict[int, tuple[float, int]] = {}
        notes: list[Note] = []
        for event in self.events:
            message = event.message
            if isinstance(message, mido.MetaMessage):
                continue
            if message.type == "note_on" and message.velocity > 0:
                pending[message.note] = (event.time, message.velocity)
            elif message.type in ("note_off", "note_on"):
                opened = pending.pop(message.note, None)
                if opened is not None:
                    start, velocity = opened
                    notes.append(Note(message.note, start, event.time, velocity))
        return notes

    def summary(self) -> dict:
        """Counts of the different message types seen."""
        counts: dict[str, int] = {}
        for event in self.events:
            key = event.message.type
            counts[key] = counts.get(key, 0) + 1
        return counts


class MidiSession:
    """Plays a reference to the keyboard and records the player's take.

    The reference is sent to the keyboard's *output* port so the player can
    hear it (piano apps usually follow the incoming MIDI), while the take is
    recorded from the *input* port.
    """

    def __init__(
        self,
        input_port: str | None = None,
        output_port: str | None = None,
        send_reference: bool = True,
    ) -> None:
        self.input_port = input_port
        self.output_port = output_port
        self.send_reference = send_reference

    def play_and_record(
        self,
        reference: Sequence,
        duration: Optional[float] = None,
        count_in_beats: int = 4,
        count_in_bpm: float = 100.0,
        on_progress: Optional[Callable[[float, int], None]] = None,
        poll: float = 0.005,
    ) -> tuple[Sequence, Sequence]:
        """Send the reference out and record what comes back.

        Returns (reference_as_played, recorded_take).
        """
        bpm = reference.tempo_map[0][1] if reference.tempo_map else 100.0
        total = duration if duration is not None else reference.duration() + 2.0

        monitor = MidiMonitor(self.input_port)
        output = None
        if self.send_reference:
            try:
                output = mido.open_output(self.output_port)
            except Exception:
                output = None

        with monitor:
            # Count-in: send metronome clicks, ignore any input.
            if count_in_beats > 0:
                self._count_in(output, count_in_beats, count_in_bpm)

            start = time.monotonic()
            notes = reference.sorted_notes()

            if output is not None:
                self._send_reference(output, notes, start)

            while True:
                time.sleep(poll)
                elapsed = time.monotonic() - start
                if on_progress is not None:
                    on_progress(elapsed, len(monitor.notes()))
                if elapsed >= total:
                    break

        if output is not None:
            output.close()

        recorded = monitor.notes()
        take = Sequence(notes=recorded, tempo_map=list(reference.tempo_map), name="take")
        return reference, take

    def _count_in(self, output, beats: int, bpm: float) -> None:
        beat = 60.0 / max(1.0, bpm)
        start = time.monotonic()
        for i in range(beats):
            at = start + i * beat
            while time.monotonic() < at:
                time.sleep(0.002)
            if output is not None:
                # Accent the downbeat.
                velocity = 100 if i == 0 else 70
                output.send(mido.Message("note_on", note=76, velocity=velocity, time=0))
                output.send(mido.Message("note_off", note=76, velocity=0, time=int(beat * 500)))

    def _send_reference(self, output, notes: list[Note], start: float) -> None:
        """Schedule the reference notes onto the output port."""
        pending: list[tuple[float, mido.Message]] = []
        for note in notes:
            velocity = max(1, min(127, int(note.velocity)))
            pending.append((note.start, mido.Message("note_on", note=note.pitch, velocity=velocity, time=0)))
            pending.append((note.end, mido.Message("note_off", note=note.pitch, velocity=0, time=0)))
        pending.sort(key=lambda item: item[0])

        for offset, message in pending:
            due = start + offset
            while time.monotonic() < due:
                time.sleep(0.001)
            output.send(message)


def pick_port(preferred: str | None, candidates: list[str], keyword: str = "") -> str | None:
    """Choose a MIDI port, preferring an exact match then a keyword match."""
    if preferred:
        return preferred
    if keyword:
        for name in candidates:
            if keyword.lower() in name.lower():
                return name
    return candidates[0] if candidates else None
