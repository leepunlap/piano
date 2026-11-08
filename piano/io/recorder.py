"""Live MIDI capture: turning a keyboard performance into a `Sequence`.

This is the front door of the system: the player sits at a keyboard, plays
along with a fixed song, and the app records what they actually played.

The recorder handles the practical details that make live capture reliable:

* it timestamps events against a monotonic clock rather than trusting the
  MIDI clock,
* it tolerates note-on without note-off (and vice versa),
* it can count-in and arm automatically,
* it reports progress so the player knows what is being recorded.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

import mido

from ..core.notes import Note, Sequence
from ..core.tempo import TempoMap, estimate_tempo_from_onsets


@dataclass
class RecorderConfig:
    """How a recording session should behave."""

    count_in_beats: int = 4
    count_in_bpm: float = 100.0
    record_overshoot: float = 2.0
    """Extra seconds to record past the expected end of the piece."""
    max_duration: float = 600.0
    """Safety cap on a single take."""
    ignore_channels: tuple[int, ...] = ()


@dataclass
class RecordingResult:
    """What a session produced."""

    sequence: Sequence
    wall_duration: float = 0.0
    events: int = 0
    dropped: int = 0

    def summary(self) -> str:
        return (
            f"recorded {len(self.sequence)} notes in {self.wall_duration:.1f}s "
            f"({self.events} events, {self.dropped} unmatched)"
        )


class MidiRecorder:
    """Captures a live performance from a MIDI input port.

    Usage:

        recorder = MidiRecorder(port_name)
        recorder.arm(expected_notes=64)
        with recorder.session() as result:
            ...  # the player performs while this block runs
    """

    def __init__(self, config: Optional[RecorderConfig] = None) -> None:
        self.config = config or RecorderConfig()
        self._pending: dict[tuple[int, int], tuple[float, int]] = {}
        self._notes: list[Note] = []
        self._events = 0
        self._dropped = 0
        self._start_time = 0.0
        self._armed = False

    # -- lifecycle --------------------------------------------------------

    def arm(self, expected_notes: int = 0) -> None:
        """Reset the recorder and get ready."""
        self._pending.clear()
        self._notes.clear()
        self._events = 0
        self._dropped = 0
        self._start_time = 0.0
        self._armed = True
        self._expected_notes = expected_notes

    def _handle(self, message: mido.Message) -> None:
        if not self._armed:
            return
        if message.channel in self.config.ignore_channels:
            return

        now = time.monotonic() - self._start_time

        if message.type == "note_on" and message.velocity > 0:
            self._pending[(message.channel, message.note)] = (now, message.velocity)
            self._events += 1
        elif message.type in ("note_off", "note_on"):
            key = (message.channel, message.note)
            opened = self._pending.pop(key, None)
            if opened is None:
                self._dropped += 1
                return
            start, velocity = opened
            self._notes.append(Note(message.note, start, now, velocity))
            self._events += 1

    # -- sessions ---------------------------------------------------------

    def record(
        self,
        port: str | None = None,
        duration: Optional[float] = None,
        on_progress: Optional[Callable[[float, int], None]] = None,
        poll: float = 0.01,
    ) -> RecordingResult:
        """Record until `duration` elapses or the expected note count is hit."""
        self.arm()
        input_port = mido.open_input(port)
        try:
            return self._run(input_port, duration, on_progress, poll)
        finally:
            input_port.close()

    def _run(
        self,
        input_port: mido.BaseInput,
        duration: Optional[float],
        on_progress: Optional[Callable[[float, int], None]],
        poll: float,
    ) -> RecordingResult:
        # Count-in.
        if self.config.count_in_beats > 0:
            self._count_in(input_port)

        self._start_time = time.monotonic()
        expected = getattr(self, "_expected_notes", 0)

        while True:
            time.sleep(poll)
            elapsed = time.monotonic() - self._start_time

            if on_progress is not None:
                on_progress(elapsed, len(self._notes))

            if duration is not None and elapsed >= duration:
                break
            if expected and len(self._notes) >= expected:
                break
            if elapsed >= self.config.max_duration:
                break

        wall = time.monotonic() - self._start_time

        # Close dangling notes at the end of the take.
        for (_, pitch), (start, velocity) in list(self._pending.items()):
            self._notes.append(Note(pitch, start, min(wall, start + 0.5), velocity))

        sequence = Sequence(
            notes=list(self._notes),
            tempo_map=[(0.0, estimate_tempo_from_onsets([n.start for n in self._notes]))],
            name="take",
        )
        self._armed = False
        return RecordingResult(
            sequence=sequence,
            wall_duration=wall,
            events=self._events,
            dropped=self._dropped,
        )

    def _count_in(self, input_port: mido.BaseInput) -> None:
        """Wait out a count-in, ignoring anything the player plays."""
        beat = 60.0 / max(1.0, self.config.count_in_bpm)
        total = self.config.count_in_beats * beat
        deadline = time.monotonic() + total
        while time.monotonic() < deadline:
            time.sleep(0.005)
        # Discard anything played during the count-in.
        self._pending.clear()


def list_ports() -> list[str]:
    """Available MIDI input ports."""
    try:
        return sorted(mido.get_input_names())
    except Exception:
        return []


def record_from_port(
    port: str | None = None,
    duration: Optional[float] = None,
    config: Optional[RecorderConfig] = None,
    on_progress: Optional[Callable[[float, int], None]] = None,
) -> RecordingResult:
    """Convenience wrapper: record one take from a port."""
    recorder = MidiRecorder(config)
    return recorder.record(port=port, duration=duration, on_progress=on_progress)
