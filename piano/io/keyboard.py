"""Real-keyboard capture: record a performance of a fixed song.

This is the module that talks to the actual instrument.  It is deliberately
separate from the analysis code so that the capture can be verified on its
own: a capture that is wrong is much harder to debug when it is tangled up
with alignment.

Responsibilities:

* pick the right MIDI port (the keyboard may expose several),
* arm and count in,
* record note events with monotonic-clock timestamps,
* optionally play the reference out to the keyboard at the same time,
* report progress while the player is performing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import mido

from ..core.notes import Note, Sequence
from ..core.tempo import TempoMap, estimate_tempo_from_onsets


@dataclass
class CaptureConfig:
    """Settings for one capture session."""

    count_in_beats: int = 4
    count_in_bpm: float = 100.0
    lead_in: float = 0.0
    """Extra seconds of silence before the reference starts (for the player
    to get ready after the count-in)."""
    trailing: float = 2.0
    """Extra seconds to keep recording after the reference ends."""
    max_duration: float = 900.0
    send_reference: bool = False
    """Send the reference as MIDI to the keyboard.

    Off by default: the guide track is played locally through the Mac's
    speakers instead, so it sounds like the instrument the final render will
    use and does not depend on the keyboard's own sound module.
    """
    reference_port: Optional[str] = None
    ignore_channels: tuple[int, ...] = ()
    min_velocity: int = 1
    """Notes below this velocity are treated as releases (MIDI convention)."""
    play_guide_audio: bool = True
    """Play the reference through the Mac's speakers."""
    guide_sfz: Optional[str] = None
    """SFZ used to render the guide track (defaults to Salamander)."""
    guide_device: Optional[str] = None
    """Audio output device for the guide track."""


@dataclass
class CaptureResult:
    """The outcome of a capture."""

    sequence: Sequence
    duration: float = 0.0
    event_count: int = 0
    unmatched: int = 0
    port: str = ""

    def summary(self) -> str:
        return (
            f"{len(self.sequence)} notes over {self.duration:.1f}s "
            f"({self.event_count} events, {self.unmatched} unmatched) via {self.port}"
        )


class KeyboardCapture:
    """Records a live performance from a MIDI keyboard."""

    def __init__(self, port: str | None = None, config: Optional[CaptureConfig] = None) -> None:
        self.port = port
        self.config = config or CaptureConfig()
        self._input: Optional[mido.BaseInput] = None
        self._output: Optional[mido.MidiOut] = None
        self._notes: list[Note] = []
        self._pending: dict[int, tuple[float, int]] = {}
        self._events = 0
        self._unmatched = 0
        self._start = 0.0
        self._recording = False

    # -- port selection ---------------------------------------------------

    @staticmethod
    def available_ports() -> list[str]:
        try:
            return sorted(mido.get_input_names())
        except Exception:
            return []

    @staticmethod
    def available_output_ports() -> list[str]:
        try:
            return sorted(mido.get_output_names())
        except Exception:
            return []

    def resolve_port(self, keyword: str = "") -> Optional[str]:
        """Pick an input port, preferring an exact match then a keyword."""
        if self.port:
            return self.port
        ports = self.available_ports()
        if not ports:
            return None
        if keyword:
            for name in ports:
                if keyword.lower() in name.lower():
                    return name
        return ports[0]

    # -- capture ----------------------------------------------------------

    def capture(
        self,
        reference: Optional[Sequence] = None,
        duration: Optional[float] = None,
        on_progress: Optional[Callable[[float, int], None]] = None,
        poll: float = 0.005,
    ) -> CaptureResult:
        """Record a take.

        If `reference` is given, the player follows it while recording:

        * by default the guide is *rendered locally* and played through the
          Mac's speakers, so it sounds like the instrument the final render
          will use (and does not depend on the keyboard's sound module),
        * with `send_reference` it is additionally sent to the keyboard as
          MIDI, which is useful if the hardware's own sound is preferred.
        """
        port = self.resolve_port()
        if port is None:
            raise RuntimeError("no MIDI input ports available")

        # The guide track is rendered *before* opening the MIDI ports: on this
        # Mac, opening an output port can disturb the input stream.
        guide_audio = None
        guide_rate = 48000
        if reference is not None and self.config.play_guide_audio:
            from .playback import render_reference

            guide_audio, guide_rate = render_reference(
                reference, self.config.guide_sfz, work_dir="work"
            )

        self._input = mido.open_input(port)
        self._output = None
        if self.config.send_reference and reference is not None:
            try:
                out_port = self.config.reference_port or (self.available_output_ports() or [None])[0]
                if out_port:
                    self._output = mido.open_output(out_port)
            except Exception:
                self._output = None

        player = None
        try:
            if guide_audio is not None:
                from .playback import AudioPlayer

                player = AudioPlayer(sample_rate=guide_rate, device=self.config.guide_device)
            return self._run(reference, duration, on_progress, poll, port, player, guide_audio)
        finally:
            if player is not None:
                player.stop()
            if self._input is not None:
                self._input.close()
                self._input = None
            if self._output is not None:
                self._output.close()
                self._output = None

    def _run(
        self,
        reference: Optional[Sequence],
        duration: Optional[float],
        on_progress: Optional[Callable[[float, int], None]],
        poll: float,
        port: str,
        player=None,
        guide_audio=None,
    ) -> CaptureResult:
        self._notes.clear()
        self._pending.clear()
        self._events = 0
        self._unmatched = 0

        # Arm the MIDI callback *before* anything else, so that nothing the
        # player plays during the count-in is lost - it is timestamped and
        # then discarded, rather than never being seen at all.
        self._start = time.monotonic()
        self._input.callback = self._on_message  # type: ignore[union-attr]
        self._recording = False

        # Count in with a metronome click.
        if self.config.count_in_beats > 0:
            self._count_in()

        # Let the player settle.
        if self.config.lead_in > 0:
            time.sleep(self.config.lead_in)

        # The take begins now.  Anything played before this point (during the
        # count-in) is discarded.
        self._start = time.monotonic()
        self._notes.clear()
        self._pending.clear()
        self._events = 0
        self._unmatched = 0
        self._recording = True

        # Start the guide track and the recording clock together.
        if player is not None and guide_audio is not None:
            player.play(guide_audio)

        if duration is None:
            if reference is not None:
                duration = reference.duration() + self.config.trailing
            else:
                duration = self.config.max_duration
        duration = min(duration, self.config.max_duration)

        scheduled: list[tuple[float, mido.Message]] = []
        if reference is not None and self._output is not None:
            scheduled = self._schedule(reference)

        while True:
            time.sleep(poll)
            elapsed = time.monotonic() - self._start

            if on_progress is not None:
                on_progress(elapsed, len(self._notes))

            if elapsed >= duration:
                break

            if scheduled:
                due = [item for item in scheduled if item[0] <= elapsed]
                if due:
                    for _, message in due:
                        self._output.send(message)  # type: ignore[union-attr]
                    scheduled = [item for item in scheduled if item[0] > elapsed]

        wall = time.monotonic() - self._start

        # Close any dangling notes.
        for pitch, (start, velocity) in list(self._pending.items()):
            self._notes.append(Note(pitch, start, min(wall, start + 0.4), velocity))

        sequence = Sequence(
            notes=list(self._notes),
            tempo_map=[(0.0, estimate_tempo_from_onsets([n.start for n in self._notes]))],
            name="take",
        )

        return CaptureResult(
            sequence=sequence,
            duration=wall,
            event_count=self._events,
            unmatched=self._unmatched,
            port=port,
        )

    def _on_message(self, message: mido.Message | mido.MetaMessage) -> None:
        if isinstance(message, mido.MetaMessage):
            return
        if message.channel in self.config.ignore_channels:
            return
        if not self._recording:
            return

        now = time.monotonic() - self._start
        self._events += 1

        if message.type == "note_on" and message.velocity >= self.config.min_velocity:
            self._pending[message.note] = (now, message.velocity)
        elif message.type in ("note_off", "note_on"):
            opened = self._pending.pop(message.note, None)
            if opened is None:
                self._unmatched += 1
                return
            start, velocity = opened
            self._notes.append(Note(message.note, start, now, velocity))

    def _count_in(self) -> None:
        """Metronome clicks before the take.

        The clicks are played locally (a short sine burst) so the player can
        hear them, and the count-in is timed so that the guide track starts
        exactly on the downbeat.
        """
        if self.config.count_in_beats <= 0:
            return

        beat = 60.0 / max(1.0, self.config.count_in_bpm)

        player = None
        try:
            from .playback import AudioPlayer

            player = AudioPlayer(device=self.config.guide_device)
        except Exception:
            player = None

        if player is not None:
            import numpy as np

            rate = 48000
            clicks = []
            for i in range(self.config.count_in_beats):
                length = int(rate * min(0.12, beat * 0.4))
                wave = np.sin(2 * np.pi * 1760.0 * np.arange(length) / rate)
                # Accent the downbeat.
                envelope = np.exp(-np.arange(length) / (rate * 0.03))
                click = (wave * envelope * (0.5 if i == 0 else 0.32)).astype(np.float32)
                clicks.append(click)
                if i < self.config.count_in_beats - 1:
                    silence = np.zeros(int(rate * (beat - length / rate)), dtype=np.float32)
                    clicks.append(silence)
            track = np.concatenate(clicks)
            player.play(np.stack([track, track], axis=1))
            # Wait out the count-in.
            time.sleep(self.config.count_in_beats * beat)
        else:
            for i in range(self.config.count_in_beats):
                at = time.monotonic() + i * beat
                while time.monotonic() < at:
                    time.sleep(0.002)

        if player is not None:
            player.stop()

        # Anything played during the count-in is discarded.

    def _schedule(self, reference: Sequence) -> list[tuple[float, mido.Message]]:
        """Build the reference's note events for playback."""
        events: list[tuple[float, mido.Message]] = []
        for note in reference.sorted_notes():
            velocity = max(1, min(127, int(note.velocity)))
            events.append((note.start, mido.Message("note_on", note=note.pitch, velocity=velocity, time=0)))
            events.append((note.end, mido.Message("note_off", note=note.pitch, velocity=0, time=0)))
        events.sort(key=lambda item: item[0])
        return events


def describe_ports() -> str:
    """A human-readable listing of the MIDI ports on this machine."""
    lines = ["MIDI inputs:"]
    inputs = KeyboardCapture.available_ports()
    lines.extend([f"  {name}" for name in inputs] or ["  (none)"])
    lines.append("MIDI outputs:")
    outputs = KeyboardCapture.available_output_ports()
    lines.extend([f"  {name}" for name in outputs] or ["  (none)"])
    return "\n".join(lines)
