"""Correcting a performance: quantisation, pitch repair and dynamics.

The guiding principle is *conservative correction*.  The goal is to remove
mistakes, not to impose a machine's interpretation.  Two rules follow:

1. A quantised note keeps the player's learned offset, so the result still
   sounds like them - just tidier.
2. A pitch is only "corrected" when there is good evidence the player meant
   the score note (same hand, same rhythmic position, close in time).  A note
   that is genuinely wrong is left alone and reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .align import Alignment, align_sequences, key_of
from .notes import Note, Sequence
from .profile import PlayerProfile
from .tempo import Grid


@dataclass
class CorrectionReport:
    """What the corrector changed, for display and for auditing."""

    pitch_fixed: list[tuple[int, int, int]] = field(default_factory=list)  # (ref_idx, from, to)
    quantised: list[tuple[int, float, float]] = field(default_factory=list)  # (ref_idx, before, after)
    added: list[Note] = field(default_factory=list)
    dropped: list[Note] = field(default_factory=list)
    unmatched_reference: int = 0
    unmatched_performed: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"pitch corrected : {len(self.pitch_fixed)}",
            f"onset quantised : {len(self.quantised)}",
            f"notes added     : {len(self.added)}",
            f"notes dropped   : {len(self.dropped)}",
            f"unmatched score : {self.unmatched_reference}",
            f"unmatched played: {self.unmatched_performed}",
        ]
        lines.extend(self.notes)
        return "\n".join(lines)


def _semitone_distance(a: int, b: int) -> int:
    return abs(a - b)


def correct_pitches(
    alignment: Alignment,
    reference: Sequence,
    performance: Sequence,
    allow_semitone_fix: bool = True,
    max_semitone_shift: int = 2,
) -> tuple[Sequence, CorrectionReport]:
    """Repair pitches using the score as ground truth.

    A performed note is rewritten to the score pitch when it is within
    `max_semitone_shift` semitones of it.  Larger deviations are treated as
    genuine wrong notes (a wrong finger, a different voice) and left in place
    but reported.
    """
    report = CorrectionReport()
    ref_strikes = reference.strikes()
    perf_strikes = performance.strikes()

    # The alignment indexes *strikes*, but the notes list is what we rewrite,
    # so translate between the two before applying any change.
    strike_positions = _strike_positions(performance)

    pitch_by_note_index: dict[int, int] = {}
    for ref_index, perf_index in alignment.pairs:
        if ref_index >= len(ref_strikes) or perf_index >= len(perf_strikes):
            continue
        ref_pitch = ref_strikes[ref_index].pitch
        perf_pitch = perf_strikes[perf_index].pitch
        distance = _semitone_distance(ref_pitch, perf_pitch)
        if distance == 0:
            continue
        if allow_semitone_fix and distance <= max_semitone_shift:
            for note_index, position in strike_positions.items():
                if position == perf_index:
                    pitch_by_note_index[note_index] = ref_pitch
                    break
            report.pitch_fixed.append((ref_index, perf_pitch, ref_pitch))
        else:
            report.notes.append(
                f"note {perf_pitch} at {perf_strikes[perf_index].start:.2f}s left as played "
                f"(score says {ref_pitch})"
            )

    fixed: list[Note] = []
    for index, note in enumerate(performance.notes):
        if index in pitch_by_note_index:
            fixed.append(note.with_pitch(pitch_by_note_index[index]))
        else:
            fixed.append(note)

    fixed_sequence = Sequence(
        notes=fixed, tempo_map=list(performance.tempo_map), name=performance.name
    )

    report.unmatched_reference = len(alignment.unmatched_reference)
    report.unmatched_performed = len(alignment.unmatched_performed)
    return fixed_sequence, report


def quantise(
    performance: Sequence,
    grid: Grid,
    profile: PlayerProfile | None = None,
    strength: float = 1.0,
    use_learned_offset: bool = True,
    score_position_by_note_index: dict[int, int] | None = None,
) -> tuple[Sequence, list[tuple[int, float, float]]]:
    """Snap onsets onto the grid.

    `strength` blends between the raw onset (0.0) and the fully snapped onset
    (1.0), which gives a natural way to do "gentle" correction.

    When `use_learned_offset` is on and the profile is confident, the player's
    own learned offset is re-applied after snapping - the difference between
    "quantised" and "quantised but still human".

    `score_position_by_note_index` maps a note index in `performance` to its
    position in the *score*.  The profile is keyed by score position, so this
    mapping is what makes the per-position offsets line up; without it the
    global average is used for every note.
    """
    changes: list[tuple[int, float, float]] = []
    out = Sequence(notes=[], tempo_map=list(performance.tempo_map), name=performance.name)

    strike_position = _strike_positions(performance)

    for index, note in enumerate(performance.notes):
        new_start = note.start
        new_end = note.end

        if note.is_strike:
            if score_position_by_note_index is not None:
                position = score_position_by_note_index.get(index)
            else:
                position = strike_position.get(index, 0)

            offset = 0.0
            if (
                profile is not None
                and use_learned_offset
                and position is not None
                and profile.is_confident(position)
            ):
                offset = profile.expected_offset(position)

            snapped = grid.snap(note.start)
            # Snap relative to the player's offset so the result lands where
            # they habitually play, not on the absolute grid.
            adjusted = grid.snap(note.start - offset) + offset
            target = snapped * (1 - strength) + adjusted * strength
            if abs(target - note.start) > 1e-6:
                changes.append((index, note.start, target))
            new_start = target
            new_end = note.end + (target - note.start)

        out.add(Note(note.pitch, new_start, new_end, note.velocity, note.hand))

    return out, changes


def _strike_positions(sequence: Sequence) -> dict[int, int]:
    """Map note index -> position within the strike list.

    The strike list is the profile's key space, so a note's position must be
    derived from the sequence's own ordering rather than assumed.  It has to
    match `Sequence.strikes()` exactly, which sorts by (onset, pitch).
    """
    return {index: position for position, index in enumerate(sequence.strike_indices())}


def apply_velocity_curve(
    performance: Sequence,
    profile: PlayerProfile | None,
    blend: float = 1.0,
) -> Sequence:
    """Re-map velocities through the player's learned curve.

    With `blend = 1.0` the raw velocities are fully replaced by the learned
    intent; with `blend = 0.0` they are left untouched.
    """
    out = Sequence(notes=[], tempo_map=list(performance.tempo_map), name=performance.name)
    for note in performance.notes:
        velocity = note.velocity
        if profile is not None and profile.velocity.count >= 3:
            mapped = profile.velocity.apply(float(note.velocity))
            velocity = int(round(velocity * (1 - blend) + mapped * blend))
        out.add(note.with_velocity(max(1, min(127, velocity))))
    return out


def fill_missing(
    reference: Sequence,
    performance: Sequence,
    alignment: Alignment,
    only_if_confident: bool = True,
) -> tuple[list[Note], list[Note]]:
    """Add score notes that the player did not play.

    Returns (notes_to_add, notes_to_drop).  Notes are only added when the
    surrounding context agrees - otherwise a missed note might really be a
    deliberate omission.
    """
    ref_strikes = reference.strikes()
    perf_strikes = performance.strikes()
    matched_perf = {perf_index for _, perf_index in alignment.pairs}

    to_add: list[Note] = []
    for ref_index in alignment.unmatched_reference:
        if ref_index >= len(ref_strikes):
            continue
        ref_note = ref_strikes[ref_index]

        # Context check: was there a performed note just before and after?
        before = [p for p in perf_strikes if p.start < ref_note.start]
        after = [p for p in perf_strikes if p.start > ref_note.start]
        if only_if_confident:
            if not before or not after:
                continue
            if ref_note.start - before[-1].start > 2.0:
                continue
            if after[0].start - ref_note.start > 2.0:
                continue

        to_add.append(ref_note)

    to_drop: list[Note] = []
    for perf_index in alignment.unmatched_performed:
        if perf_index >= len(perf_strikes):
            continue
        to_drop.append(perf_strikes[perf_index])

    return to_add, to_drop


def score_position_map(performance: Sequence, alignment: Alignment) -> dict[int, int]:
    """Map note index in `performance` -> strike position in the score.

    The profile is keyed by score position, but the corrector works on the
    performed notes, so this translation is what lets a per-position learned
    offset be applied to the right note.
    """
    strike_position = _strike_positions(performance)
    mapping: dict[int, int] = {}
    for ref_index, perf_index in alignment.pairs:
        for note_index, position in strike_position.items():
            if position == perf_index:
                mapping[note_index] = ref_index
                break
    return mapping


def correct_performance(
    reference: Sequence,
    performance: Sequence,
    grid: Grid,
    profile: PlayerProfile | None = None,
    quantise_strength: float = 1.0,
    fix_pitches: bool = True,
    add_missing: bool = False,
    velocity_blend: float = 1.0,
    alignment: Alignment | None = None,
    strict: bool = False,
) -> tuple[Sequence, CorrectionReport]:
    """Full correction pass over one performance.

    Order matters: pitches are fixed first (so that pitch-based reasoning
    downstream sees the intended notes), then velocities are re-mapped, then
    onsets are quantised.

    `strict` controls the timing philosophy:

    * `strict=False` (default) removes the player's *jitter* but keeps their
      learned systematic offset, so the result still sounds like them.
    * `strict=True` snaps everything exactly onto the grid, which is what
      you want when the output must match a fixed song precisely.
    """
    if alignment is None:
        alignment = align_sequences(reference, performance)

    report = CorrectionReport()
    current = performance

    if fix_pitches:
        current, pitch_report = correct_pitches(alignment, reference, current)
        report.pitch_fixed = pitch_report.pitch_fixed
        report.notes.extend(pitch_report.notes)
        report.unmatched_reference = pitch_report.unmatched_reference
        report.unmatched_performed = pitch_report.unmatched_performed

    if profile is not None:
        current = apply_velocity_curve(current, profile, velocity_blend)

    # Build the score-position mapping *before* quantising, using the
    # alignment of the original performance (note indices are preserved by
    # the pitch and velocity passes).
    position_map = score_position_map(performance, alignment)

    current, quantised_changes = quantise(
        current,
        grid,
        profile=profile,
        strength=quantise_strength,
        use_learned_offset=not strict,
        score_position_by_note_index=position_map,
    )
    report.quantised = quantised_changes

    if add_missing:
        additions, drops = fill_missing(reference, current, alignment)
        if additions:
            current = Sequence(
                notes=list(current.notes) + additions,
                tempo_map=list(current.tempo_map),
                name=current.name,
            )
            report.added = additions
        if drops:
            keep = [n for n in current.notes if n not in drops]
            current = Sequence(
                notes=keep, tempo_map=list(current.tempo_map), name=current.name
            )
            report.dropped = drops

    return current, report
