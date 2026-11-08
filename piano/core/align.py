"""Score following: aligning a played take to a reference score.

This is the stage that answers "which note did the player actually mean?".
Without it, every later stage is guessing.

Two levels are provided:

* `align_sequences` - offline DTW over note-onset sequences, used to analyse
  a finished take.  This is the reference implementation and the one used to
  train the player's profile.
* `OnlineTracker` - a streaming, note-at-a-time matcher with a small search
  window, used when the correction has to happen while the player is still
  playing.

Both produce an `Alignment`: a mapping from reference-note index to
performed-note index (or None if the note was not played).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from .notes import Note, Sequence


@dataclass
class Alignment:
    """Result of matching a performance against a score.

    `pairs` maps (reference_index, performed_index) for notes that were matched.
    `unmatched_reference` and `unmatched_performed` list the indices left over.
    """

    pairs: list[tuple[int, int]] = field(default_factory=list)
    unmatched_reference: list[int] = field(default_factory=list)
    unmatched_performed: list[int] = field(default_factory=list)
    cost: float = 0.0

    def matched_pairs(self) -> list[tuple[int, int]]:
        return self.pairs

    def __len__(self) -> int:
        return len(self.pairs)

    @property
    def coverage(self) -> float:
        """Fraction of reference notes that were matched."""
        total = len(self.pairs) + len(self.unmatched_reference)
        return len(self.pairs) / total if total else 0.0


def _local_cost(
    ref: Note,
    perf: Note,
    pitch_weight: float,
    time_weight: float,
    time_tolerance: float,
) -> float:
    """Cost of matching two notes.

    The two errors are scaled so they are directly comparable with the skip
    penalty:

    * Time is measured in units of `time_tolerance` (a typical tolerance
      window).  A note played 45 ms late inside a 150 ms tolerance costs
      ~0.09, so matching is still far cheaper than skipping.
    * Pitch is penalised quadratically per semitone but gently enough that a
      slightly wrong note still aligns to its slot - that is exactly the case
      the pitch corrector needs to see.
    """
    semitones = abs(ref.pitch - perf.pitch)
    pitch_cost = 0.25 * semitones * semitones
    delta = (ref.start - perf.start) / max(1e-6, time_tolerance)
    time_cost = delta * delta
    return pitch_weight * pitch_cost + time_weight * time_cost


def align_sequences(
    reference: Sequence,
    performance: Sequence,
    pitch_weight: float = 1.0,
    time_weight: float = 1.0,
    time_tolerance: float = 0.15,
    max_time_gap: float = 2.0,
    skip_penalty: float = 1.0,
) -> Alignment:
    """Align a performance to a reference score with dynamic time warping.

    Notes are reduced to onsets (strikes).  The cost of matching a reference
    note to a performed note combines pitch error and onset error; skipping a
    note in either sequence carries a fixed penalty.

    `max_time_gap` bounds how far apart a matched pair may be in time, which
    keeps the alignment from pairing a note with something wildly distant.
    """
    ref_notes = reference.strikes()
    perf_notes = performance.strikes()

    n, m = len(ref_notes), len(perf_notes)
    if n == 0 or m == 0:
        return Alignment(
            unmatched_reference=list(range(n)),
            unmatched_performed=list(range(m)),
        )

    # dp[i][j] = cost of aligning ref[:i] with perf[:j]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]  # 0=skip_ref, 1=skip_perf, 2=match

    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + skip_penalty
        back[i][0] = 0
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + skip_penalty
        back[0][j] = 1

    for i in range(1, n + 1):
        ref = ref_notes[i - 1]
        for j in range(1, m + 1):
            perf = perf_notes[j - 1]

            gap = abs(ref.start - perf.start)
            if gap > max_time_gap:
                match_cost = float("inf")
            else:
                match_cost = dp[i - 1][j - 1] + _local_cost(
                    ref, perf, pitch_weight, time_weight, time_tolerance
                )

            skip_ref_cost = dp[i - 1][j] + skip_penalty
            skip_perf_cost = dp[i][j - 1] + skip_penalty

            best = min(match_cost, skip_ref_cost, skip_perf_cost)
            dp[i][j] = best
            if best == match_cost:
                back[i][j] = 2
            elif best == skip_ref_cost:
                back[i][j] = 0
            else:
                back[i][j] = 1

    # Walk the backtrace.
    pairs: list[tuple[int, int]] = []
    unmatched_ref: list[int] = []
    unmatched_perf: list[int] = []

    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j]
        if move == 2:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif move == 0 and i > 0:
            unmatched_ref.append(i - 1)
            i -= 1
        elif j > 0:
            unmatched_perf.append(j - 1)
            j -= 1
        else:
            break

    pairs.reverse()
    unmatched_ref.reverse()
    unmatched_perf.reverse()

    return Alignment(
        pairs=pairs,
        unmatched_reference=unmatched_ref,
        unmatched_performed=unmatched_perf,
        cost=dp[n][m],
    )


def pitch_histogram(sequence: Sequence) -> dict[int, int]:
    """Count of onsets per MIDI pitch."""
    histogram: dict[int, int] = {}
    for note in sequence.strikes():
        histogram[note.pitch] = histogram.get(note.pitch, 0) + 1
    return histogram


def key_of(sequence: Sequence) -> Optional[str]:
    """Best-guess key of a sequence, using pitch-class frequency.

    Returns e.g. "C major" / "a minor".  Used to sanity-check pitch
    corrections: a note that would push the piece out of key is suspect.
    """
    histogram = pitch_histogram(sequence)
    if not histogram:
        return None

    weights = {
        "C": [0, 2, 4, 5, 7, 9, 11], "G": [0, 2, 4, 6, 7, 9, 11],
        "D": [0, 2, 4, 5, 7, 9, 10], "A": [0, 2, 3, 5, 7, 9, 10],
        "E": [0, 2, 4, 5, 7, 8, 10], "B": [0, 1, 3, 5, 6, 8, 10],
        "F#": [0, 2, 3, 5, 7, 8, 10],
        "F": [0, 1, 3, 5, 7, 8, 10],
    }
    major_profiles = {
        "C": [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
        "G": [6.33, 2.68, 3.52, 2.60, 4.38, 4.09, 2.52, 5.38, 2.43, 3.65, 2.38, 2.88],
        "D": [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
        "F": [5.38, 2.38, 3.35, 2.23, 4.09, 3.02, 2.52, 4.38, 2.48, 4.06, 2.29, 2.88],
    }
    minor_profiles = {
        "A": [6.33, 2.68, 3.52, 5.38, 2.60, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.88],
        "E": [6.33, 2.68, 3.52, 5.38, 2.60, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.88],
        "D": [6.33, 2.23, 3.48, 5.38, 2.60, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.88],
        "B": [6.33, 2.68, 3.52, 4.38, 5.38, 2.60, 4.09, 2.52, 5.19, 2.39, 3.66, 2.88],
    }

    counts = [0.0] * 12
    for pitch, count in histogram.items():
        counts[pitch % 12] += count
    total = sum(counts) or 1.0
    normalised = [c / total for c in counts]

    def correlation(profile: Sequence[float], tonic_pc: int) -> float:
        rotated = profile[tonic_pc:] + profile[:tonic_pc]
        mean_x = sum(normalised) / 12.0
        mean_y = sum(rotated) / 12.0
        num = sum((normalised[i] - mean_x) * (rotated[i] - mean_y) for i in range(12))
        den_x = sum((c - mean_x) ** 2 for c in normalised) ** 0.5
        den_y = sum((c - mean_y) ** 2 for c in rotated) ** 0.5
        if den_x < 1e-9 or den_y < 1e-9:
            return 0.0
        return num / (den_x * den_y)

    best = (-2.0, "C major")
    for tonic, profile in major_profiles.items():
        tonic_pc = {"C": 0, "G": 7, "D": 2, "F": 5}[tonic]
        score = correlation(profile, tonic_pc)
        if score > best[0]:
            best = (score, f"{tonic} major")
    for tonic, profile in minor_profiles.items():
        tonic_pc = {"A": 9, "E": 4, "D": 2, "B": 11}[tonic]
        score = correlation(profile, tonic_pc)
        if score > best[0]:
            best = (score, f"{tonic} minor")
    return best[1]
