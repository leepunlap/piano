"""Online note tracking: matching a live performance to the score in real time.

The offline DTW in `align.py` is exact but needs the whole take.  For live
correction we need a decision *now*: given the note the player just played,
which score note was it?

The tracker keeps a small window of candidate score notes around the current
playback position and picks the best match by the same cost model used
offline, with a hysteresis rule so that a note is not "claimed" twice.

State machine per expected note:

    PENDING -> CLAIMED -> CONSUMED

A note that has been consumed cannot be claimed again, which is what stops a
repeated note (the same pitch played twice in a row) from being matched to the
same score slot twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .notes import Note, Sequence


class NoteState(Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    CONSUMED = "consumed"


@dataclass
class TrackerConfig:
    """Tuning for the online tracker."""

    time_tolerance: float = 0.15
    """How far (seconds) a played note may be from the expected one."""
    lookahead: float = 0.5
    """How far ahead of the playhead to consider score notes."""
    lookbehind: float = 0.5
    """How far behind the playhead to still consider score notes."""
    pitch_weight: float = 1.0
    time_weight: float = 1.0
    max_semitone_shift: int = 2
    """Largest pitch error that is still considered a match."""
    commit_delay: float = 0.08
    """Grace period before a claimed note is finalised."""


@dataclass
class MatchResult:
    """What the tracker decided about a played note."""

    matched: bool
    expected_index: Optional[int] = None
    expected_pitch: Optional[int] = None
    played_pitch: int = 0
    time_error: float = 0.0
    pitch_error: int = 0
    confidence: float = 0.0
    reason: str = ""


@dataclass
class OnlineTracker:
    """Matches played notes to score notes as they arrive."""

    reference: Sequence
    config: TrackerConfig = field(default_factory=TrackerConfig)

    _states: list[NoteState] = field(default_factory=list)
    _expected: list[Note] = field(default_factory=list)
    _claimed_at: dict[int, float] = field(default_factory=dict)
    _playhead: float = 0.0

    def __post_init__(self) -> None:
        self._expected = self.reference.strikes()
        self._states = [NoteState.PENDING] * len(self._expected)

    # -- lifecycle --------------------------------------------------------

    def reset(self) -> None:
        self._states = [NoteState.PENDING] * len(self._expected)
        self._claimed_at.clear()
        self._playhead = 0.0

    def advance(self, seconds: float) -> None:
        """Move the playhead forward (the transport clock)."""
        self._playhead += seconds

    # -- matching ---------------------------------------------------------

    def feed(self, pitch: int, time_s: float, velocity: int = 80) -> MatchResult:
        """Offer a played note to the tracker.

        `time_s` is the note's onset time on the same clock as the reference.
        """
        best_index: Optional[int] = None
        best_cost = float("inf")
        best_error = 0.0
        best_pitch_error = 0

        window_start = time_s - self.config.lookbehind
        window_end = time_s + self.config.lookahead

        for index, expected in enumerate(self._expected):
            if self._states[index] is not NoteState.PENDING:
                continue
            if not (window_start <= expected.start <= window_end):
                continue

            time_error = time_s - expected.start
            if abs(time_error) > self.config.time_tolerance:
                continue

            pitch_error = abs(pitch - expected.pitch)
            if pitch_error > self.config.max_semitone_shift:
                continue

            cost = (
                self.config.pitch_weight * 0.25 * pitch_error * pitch_error
                + self.config.time_weight * (time_error / self.config.time_tolerance) ** 2
            )
            if cost < best_cost:
                best_cost = cost
                best_index = index
                best_error = time_error
                best_pitch_error = pitch_error

        if best_index is None:
            return MatchResult(
                matched=False,
                played_pitch=pitch,
                reason="no candidate within the search window",
            )

        self._states[best_index] = NoteState.CLAIMED
        self._claimed_at[best_index] = time_s

        # Confidence: 1.0 is a perfect match, decaying with the normalised cost.
        confidence = 1.0 / (1.0 + best_cost)

        return MatchResult(
            matched=True,
            expected_index=best_index,
            expected_pitch=self._expected[best_index].pitch,
            played_pitch=pitch,
            time_error=best_error,
            pitch_error=best_pitch_error,
            confidence=confidence,
            reason="matched",
        )

    def finalise(self, now: float) -> list[int]:
        """Commit claimed notes that have been stable long enough.

        Returns the indices that were committed (i.e. are now consumed).
        """
        committed: list[int] = []
        for index, claimed_time in list(self._claimed_at.items()):
            if now - claimed_time >= self.config.commit_delay:
                self._states[index] = NoteState.CONSUMED
                self._claimed_at.pop(index, None)
                committed.append(index)
        return committed

    # -- introspection ----------------------------------------------------

    def state(self, index: int) -> NoteState:
        return self._states[index]

    def pending_indices(self) -> list[int]:
        return [i for i, state in enumerate(self._states) if state is NoteState.PENDING]

    def consumed_indices(self) -> list[int]:
        return [i for i, state in enumerate(self._states) if state is NoteState.CONSUMED]

    def progress(self) -> float:
        """Fraction of the score consumed so far."""
        if not self._states:
            return 0.0
        return len(self.consumed_indices()) / len(self._states)

    def next_expected(self) -> Optional[Note]:
        for index, state in enumerate(self._states):
            if state is NoteState.PENDING:
                return self._expected[index]
        return None
