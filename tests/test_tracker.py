"""Test the online (real-time) tracker against the offline aligner."""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from piano.core.align import align_sequences
from piano.core.notes import Note, Sequence
from piano.core.tracker import NoteState, OnlineTracker, TrackerConfig
from piano.core.tempo import TempoMap
from tests.test_pipeline import build_score, human_take

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name} {detail}")


def test_tracker_matches_offline() -> None:
    print("\n[1] Online tracker vs offline alignment")
    score = build_score()
    take = human_take(score, seed=11, mean_lag=0.045, lag_spread=0.015,
                      wrong_note_chance=0.10, missed_note_chance=0.08)

    tracker = OnlineTracker(score, TrackerConfig())
    results = []
    for note in take.strikes():
        result = tracker.feed(note.pitch, note.start, note.velocity)
        results.append(result)
        tracker.finalise(note.start + 0.2)

    matched = [r for r in results if r.matched]
    offline = align_sequences(score, take)

    check("tracker matched most notes", len(matched) >= 0.8 * len(offline.pairs),
          f"{len(matched)} online vs {len(offline.pairs)} offline")

    # The two should agree on which score slots were used.
    online_slots = sorted(r.expected_index for r in matched)
    offline_slots = sorted(ri for ri, _ in offline.pairs)
    overlap = len(set(online_slots) & set(offline_slots))
    check("online and offline agree on slots", overlap >= 0.85 * len(offline_slots),
          f"{overlap} of {len(offline_slots)}")

    check("each slot used at most once", len(online_slots) == len(set(online_slots)))


def test_tracker_rejects_wrong_notes() -> None:
    print("\n[2] Tracker rejects notes that don't belong")
    score = build_score()
    tracker = OnlineTracker(score, TrackerConfig())

    # A note far outside the window.
    result = tracker.feed(60, 100.0, 80)
    check("note far outside the window is rejected", not result.matched)

    # A note with an impossible pitch for the slot.
    tracker2 = OnlineTracker(score, TrackerConfig())
    first = score.strikes()[0]
    result2 = tracker2.feed(first.pitch + 7, first.start, 80)
    check("note with a huge pitch error is rejected", not result2.matched,
          f"pitch error {result2.pitch_error}")


def test_tracker_measures_error() -> None:
    print("\n[3] Tracker reports timing error")
    score = build_score()
    tracker = OnlineTracker(score, TrackerConfig())
    first = score.strikes()[0]

    result = tracker.feed(first.pitch, first.start + 0.045, 80)
    check("note matched", result.matched)
    check("timing error measured", abs(result.time_error - 0.045) < 1e-6,
          f"{result.time_error:.4f}")
    check("confidence is high for a close match", result.confidence > 0.8,
          f"{result.confidence:.3f}")


def test_tracker_states() -> None:
    print("\n[4] Tracker state machine")
    score = build_score()
    tracker = OnlineTracker(score, TrackerConfig())
    first = score.strikes()[0]

    check("starts pending", tracker.state(0) is NoteState.PENDING)

    tracker.feed(first.pitch, first.start, 80)
    check("becomes claimed", tracker.state(0) is NoteState.CLAIMED)

    tracker.finalise(first.start + 0.2)
    check("becomes consumed", tracker.state(0) is NoteState.CONSUMED)

    # A second note in the same slot must not match again.
    result = tracker.feed(first.pitch, first.start + 0.05, 80)
    check("consumed slot cannot be reused", not result.matched)

    check("progress advances", tracker.progress() > 0)
    tracker.reset()
    check("reset clears state", tracker.state(0) is NoteState.PENDING and tracker.progress() == 0)


def test_tracker_handles_repeats() -> None:
    print("\n[5] Tracker handles repeated pitches")
    # Two notes with the same pitch in a row.
    notes = [Note(60, 0.0, 0.5, 80), Note(60, 0.6, 1.1, 80), Note(60, 1.2, 1.7, 80)]
    score = Sequence(notes=notes, tempo_map=[(0.0, 100.0)], name="repeats")

    tracker = OnlineTracker(score, TrackerConfig())
    results = [tracker.feed(60, n.start, 80) for n in notes]
    tracker.finalise(3.0)

    check("all three repeats matched", all(r.matched for r in results),
          f"{[r.matched for r in results]}")
    slots = [r.expected_index for r in results if r.matched]
    check("each repeat got its own slot", sorted(slots) == [0, 1, 2], f"{slots}")


def main() -> int:
    print("=" * 68)
    print("Online tracker tests")
    print("=" * 68)

    test_tracker_matches_offline()
    test_tracker_rejects_wrong_notes()
    test_tracker_measures_error()
    test_tracker_states()
    test_tracker_handles_repeats()

    print("\n" + "=" * 68)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
