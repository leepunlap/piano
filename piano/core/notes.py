"""Core MIDI note representation shared across the pipeline.

A `Note` is the atomic unit of both the reference score and the player's
performance.  Keeping one representation for both sides is what lets the
alignment stage compare them directly.

Times are in seconds (float).  Velocities are 0..127 (MIDI scale), matching
the SFZ `lovel`/`hivel` convention used by the sample libraries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(frozen=True, order=True)
class Note:
    """A single note event.

    Attributes:
        pitch: MIDI note number (0-127, 60 = middle C).
        start: onset time in seconds from the start of the sequence.
        end: offset time in seconds.  Equal to `start` for a zero-length note.
        velocity: MIDI velocity 1..127.
        hand: optional label ("right"/"left") used to split score tracks.
    """

    pitch: int
    start: float
    end: float
    velocity: int = 80
    hand: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def is_strike(self) -> bool:
        """True if this is an onset rather than a sustain."""
        return self.duration > 1e-9

    def shifted(self, seconds: float) -> "Note":
        return Note(
            pitch=self.pitch,
            start=self.start + seconds,
            end=self.end + seconds,
            velocity=self.velocity,
            hand=self.hand,
        )

    def with_pitch(self, pitch: int) -> "Note":
        return Note(pitch, self.start, self.end, self.velocity, self.hand)

    def with_velocity(self, velocity: int) -> "Note":
        return Note(self.pitch, self.start, self.end, velocity, self.hand)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Note(pitch={self.pitch}, start={self.start:.4f}, "
            f"end={self.end:.4f}, vel={self.velocity})"
        )


@dataclass
class Sequence:
    """An ordered collection of notes plus timing metadata.

    `tempo_map` holds (time_seconds, beats_per_minute) pairs describing the
    reference grid, so that quantisation can work in musical time rather than
    in raw seconds.
    """

    notes: list[Note] = field(default_factory=list)
    tempo_map: list[tuple[float, float]] = field(default_factory=list)
    name: str = ""

    def __len__(self) -> int:
        return len(self.notes)

    def __iter__(self):
        return iter(self.notes)

    def add(self, note: Note) -> None:
        self.notes.append(note)

    def sorted_notes(self) -> list[Note]:
        """Notes ordered by onset, then pitch.  Stable and deterministic."""
        return sorted(self.notes, key=lambda n: (n.start, n.pitch))

    def strike_indices(self) -> list[int]:
        """Indices into `self.notes` of the onsets, in time order.

        This is the canonical "strike order" used everywhere - profile keys,
        alignment indices and corrections all refer to positions in this list.
        """
        ordered = sorted(
            (index for index, note in enumerate(self.notes) if note.is_strike),
            key=lambda index: (self.notes[index].start, self.notes[index].pitch),
        )
        return ordered

    def strikes(self) -> list[Note]:
        """Onsets only, in time order - the events a player actually hits."""
        return [self.notes[index] for index in self.strike_indices()]

    def duration(self) -> float:
        if not self.notes:
            return 0.0
        return max(n.end for n in self.notes)

    def pitch_range(self) -> tuple[int, int]:
        if not self.notes:
            return (60, 60)
        pitches = [n.pitch for n in self.notes]
        return (min(pitches), max(pitches))

    def extend(self, other: "Sequence") -> None:
        self.notes.extend(other.notes)

    def slice(self, start: float, end: float) -> "Sequence":
        """Return the notes whose onset falls within [start, end)."""
        return Sequence(
            notes=[n for n in self.notes if start <= n.start < end],
            tempo_map=list(self.tempo_map),
            name=self.name,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Sequence(name={self.name!r}, notes={len(self.notes)}, dur={self.duration():.2f}s)"


def group_by_hand(sequence: Sequence) -> dict[str, Sequence]:
    """Split a sequence into per-hand sequences based on `Note.hand`."""
    out: dict[str, list[Note]] = {}
    for note in sequence.notes:
        out.setdefault(note.hand or "all", []).append(note)
    return {
        hand: Sequence(notes=notes, tempo_map=list(sequence.tempo_map), name=f"{sequence.name}:{hand}")
        for hand, notes in out.items()
    }


def merge_sequences(sequences: Iterable[Sequence]) -> Sequence:
    """Concatenate sequences into one, keeping the first tempo map."""
    merged = Sequence()
    it = iter(sequences)
    for seq in it:
        merged.extend(seq)
        if not merged.tempo_map:
            merged.tempo_map = list(seq.tempo_map)
    return merged
