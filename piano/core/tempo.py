"""Musical time: tempo maps, beat grids and quantisation.

Quantising in *musical* time rather than raw seconds matters because the
reference songs are in time, not in milliseconds.  The functions here convert
between seconds and beat positions using a tempo map, and snap beat positions
onto a subdivision grid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

DEFAULT_BPM = 120.0


@dataclass
class TempoMap:
    """A piecewise-constant tempo map.

    `changes` is a list of (time_seconds, bpm) sorted by time.  The first entry
    defines the tempo from t=0 up to the next change.
    """

    changes: list[tuple[float, float]] = field(default_factory=list)

    @classmethod
    def from_sequence(cls, tempo_map: Sequence[tuple[float, float]]) -> "TempoMap":
        return cls(sorted(tempo_map, key=lambda c: c[0]))

    @classmethod
    def constant(cls, bpm: float = DEFAULT_BPM) -> "TempoMap":
        return cls([(0.0, bpm)])

    def bpm_at(self, time_s: float) -> float:
        bpm = self.changes[0][1] if self.changes else DEFAULT_BPM
        for change_time, change_bpm in self.changes:
            if change_time <= time_s:
                bpm = change_bpm
            else:
                break
        return bpm

    def beats_at(self, time_s: float) -> float:
        """Absolute beat position of a time in seconds."""
        if not self.changes:
            return time_s * DEFAULT_BPM / 60.0
        beats = 0.0
        current_time = 0.0
        current_bpm = self.changes[0][1]
        for change_time, change_bpm in self.changes:
            if change_time <= time_s:
                if change_time > current_time:
                    beats += (change_time - current_time) * current_bpm / 60.0
                current_time = change_time
                current_bpm = change_bpm
            else:
                break
        beats += (time_s - current_time) * current_bpm / 60.0
        return beats

    def time_at(self, beats: float) -> float:
        """Inverse of `beats_at`: the time in seconds of a beat position."""
        if not self.changes:
            return beats * 60.0 / DEFAULT_BPM
        current_time = 0.0
        current_bpm = self.changes[0][1]
        accumulated = 0.0
        for change_time, change_bpm in self.changes:
            boundary_beats = accumulated + (change_time - current_time) * current_bpm / 60.0
            if beats <= boundary_beats:
                return current_time + (beats - accumulated) * 60.0 / current_bpm
            accumulated = boundary_beats
            current_time = change_time
            current_bpm = change_bpm
        return current_time + (beats - accumulated) * 60.0 / current_bpm


@dataclass
class Grid:
    """A quantisation grid.

    `divisions` is how many grid steps make up one beat (4 = sixteenths,
    2 = eighths, 1 = quarter notes).
    """

    tempo: TempoMap
    divisions: int = 4
    offset: float = 0.0

    def beat_of(self, time_s: float) -> float:
        return self.tempo.beats_at(time_s) - self.offset

    def step_of(self, time_s: float) -> float:
        return self.beat_of(time_s) * self.divisions

    def snap(self, time_s: float) -> float:
        """Nearest grid position, returned in seconds."""
        step = self.step_of(time_s)
        snapped_step = round(step)
        return self.tempo.time_at(snapped_step / self.divisions + self.offset)

    def snap_with_offset(self, time_s: float, offset: float) -> float:
        """Snap to the grid, then re-add a learned human offset.

        This is what keeps a quantised take from sounding robotic: the
        correction removes the *error* but restores the player's own tendency
        to sit slightly behind or ahead of the beat.
        """
        return self.snap(time_s) + offset

    def step_duration(self) -> float:
        """Seconds per grid step at the current tempo."""
        return 60.0 / self.tempo.bpm_at(0.0) / self.divisions


def grid_from_sequence(tempo_map: Sequence[tuple[float, float]], divisions: int = 4, offset: float = 0.0) -> Grid:
    return Grid(TempoMap.from_sequence(tempo_map), divisions=divisions, offset=offset)


def estimate_tempo_from_onsets(times: Sequence[float]) -> float:
    """Rough BPM estimate from inter-onset intervals.

    Used when a reference MIDI has no tempo map of its own.  Looks at the
    median inter-onset interval and picks the nearest plausible tempo in a
    60-180 BPM range.
    """
    if len(times) < 2:
        return DEFAULT_BPM

    intervals = sorted(b - a for a, b in zip(times, times[1:]) if b - a > 1e-6)
    if not intervals:
        return DEFAULT_BPM

    # Median is robust against both fast runs and long rests.
    median = intervals[len(intervals) // 2]
    if median <= 0:
        return DEFAULT_BPM

    raw_bpm = 60.0 / median
    while raw_bpm < 60.0:
        raw_bpm *= 2.0
    while raw_bpm > 180.0:
        raw_bpm /= 2.0
    return raw_bpm
