"""End-to-end test of the accompaniment pipeline.

Builds a synthetic "score", plays it back with realistic human imperfections
(late onset, wrong note, missed note, loose velocity), then checks that the
corrector repairs what it should and leaves alone what it should.

Run with:  python3 -m tests.test_pipeline
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from piano.core.align import (
    align_sequences,
    detect_octave_shift,
    find_global_offset,
    key_of,
)
from piano.core.correct import correct_performance, correct_pitches, quantise
from piano.core.notes import Note, Sequence
from piano.core.profile import PlayerProfile
from piano.core.tempo import Grid, TempoMap
from piano.io.midi_io import read_midi, write_midi, write_midi_with_tempo_map

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name} {detail}")


def build_score() -> Sequence:
    """A simple C-major scale phrase, 4/4 at 100 BPM.

    Velocities vary so the velocity-regression stage has something to learn.
    """
    notes: list[Note] = []
    scale = [60, 62, 64, 65, 67, 69, 71, 72]
    for bar in range(4):
        for i, pitch in enumerate(scale):
            start = (bar * 8 + i) * 0.5 * 60.0 / 100.0
            # Dynamic shape: strong downbeats, softer inner notes.
            velocity = 100 if i == 0 else 60 + (i * 3)
            notes.append(Note(pitch, start, start + 0.45, velocity))
    return Sequence(notes=notes, tempo_map=[(0.0, 100.0)], name="scale")


def human_take(
    score: Sequence,
    seed: int = 7,
    mean_lag: float = 0.045,
    lag_spread: float = 0.018,
    wrong_note_chance: float = 0.10,
    missed_note_chance: float = 0.08,
    velocity_scale: float = 0.78,
) -> Sequence:
    """Simulate a player: consistent lag, jitter, occasional errors."""
    rng = random.Random(seed)
    notes: list[Note] = []
    for note in score.notes:
        if rng.random() < missed_note_chance:
            continue
        start = note.start + mean_lag + rng.gauss(0.0, lag_spread)
        pitch = note.pitch
        if rng.random() < wrong_note_chance:
            pitch = note.pitch + rng.choice([-2, -1, 1, 2])
        velocity = max(1, min(127, int(round(note.velocity * velocity_scale + rng.gauss(0, 4)))))
        notes.append(Note(pitch, start, start + note.duration, velocity))
    return Sequence(notes=notes, tempo_map=list(score.tempo_map), name="take")


def test_midi_roundtrip() -> None:
    print("\n[1] MIDI round-trip")
    score = build_score()
    path = "/tmp/piano_test_score.mid"
    write_midi_with_tempo_map(score, path)
    loaded = read_midi(path)

    check("note count preserved", len(loaded) == len(score), f"{len(loaded)} vs {len(score)}")

    original = sorted((n.pitch, round(n.start, 4)) for n in score.notes)
    restored = sorted((n.pitch, round(n.start, 4)) for n in loaded.notes)
    check("pitches and onsets preserved", original == restored)

    tempo = loaded.tempo_map[0][1]
    check("tempo preserved", abs(tempo - 100.0) < 0.5, f"got {tempo}")


def test_alignment() -> None:
    print("\n[2] Alignment")
    score = build_score()
    take = human_take(score, mean_lag=0.045)

    alignment = align_sequences(score, take)
    check("most notes matched", alignment.coverage > 0.85, f"coverage {alignment.coverage:.2f}")

    ref_strikes = score.strikes()
    perf_strikes = take.strikes()
    errors = [
        perf_strikes[p].start - ref_strikes[r].start
        for r, p in alignment.pairs
        if r < len(ref_strikes) and p < len(perf_strikes)
    ]
    mean_error = sum(errors) / len(errors)
    check("recovers the player's lag", 0.03 < mean_error < 0.06, f"mean {mean_error:.4f}s")

    wrong = [
        (r, p)
        for r, p in alignment.pairs
        if ref_strikes[r].pitch != perf_strikes[p].pitch
    ]
    check("wrong notes still align to their slot", len(wrong) >= 1, f"{len(wrong)} detected")


def test_profile_learning() -> None:
    print("\n[3] Profile learning")
    score = build_score()
    profile = PlayerProfile(player_id="test", song_id="scale")

    for i in range(5):
        take = human_take(score, seed=100 + i, mean_lag=0.045 + i * 0.002)
        alignment = align_sequences(score, take)
        profile.observe(alignment, score, take)

    check("takes recorded", profile.takes == 5, f"{profile.takes}")
    check("learned a lag", 0.03 < profile.global_timing.mean < 0.07, f"{profile.global_timing.mean:.4f}")
    check("jitter is small", profile.global_timing.std < 0.04, f"{profile.global_timing.std:.4f}")
    check("profile is confident", profile.is_confident())
    check("velocity slope learned", 0.5 < profile.velocity.slope < 1.1, f"{profile.velocity.slope:.3f}")

    path = "/tmp/piano_test_profile.json"
    profile.save(path)
    reloaded = PlayerProfile.load(path)
    check("profile round-trips", reloaded.takes == profile.takes and abs(
        reloaded.global_timing.mean - profile.global_timing.mean) < 1e-5)

    print("      " + profile.summary().replace("\n", "\n      "))


def test_quantise() -> None:
    print("\n[4] Quantisation")
    score = build_score()
    take = human_take(score, mean_lag=0.045, lag_spread=0.030)
    grid = Grid(TempoMap.constant(100.0), divisions=2)  # eighth notes
    profile = PlayerProfile()

    quantised, changes = quantise(take, grid, profile=None)
    starts = [n.start for n in quantised.strikes()]
    off_grid = [
        s for s, n in zip(starts, quantised.strikes())
        if abs(((s * 100.0 / 60.0) % 0.5) - 0.0) > 1e-3 and abs(((s * 100.0 / 60.0) % 0.5) - 0.5) > 1e-3
    ]
    check("everything lands on the grid", len(off_grid) == 0, f"{len(off_grid)} off-grid")

    raw_error = _mean_abs_error(take, score)
    quant_error = _mean_abs_error(quantised, score)
    check("quantisation reduces timing error", quant_error < raw_error,
          f"{raw_error:.4f} -> {quant_error:.4f}")
    check("changes were recorded", len(changes) > 0)


def test_quantise_keeps_human_offset() -> None:
    print("\n[5] Quantisation preserves the player's feel")
    score = build_score()
    grid = Grid(TempoMap.constant(100.0), divisions=2)
    profile = PlayerProfile()

    take = human_take(score, mean_lag=0.045, lag_spread=0.010)
    alignment = align_sequences(score, take)
    profile.observe(alignment, score, take)

    flat, _ = quantise(take, grid, profile=None)
    human, _ = quantise(take, grid, profile=profile, use_learned_offset=True)

    flat_error = _mean_signed_error(flat, score)
    human_error = _mean_signed_error(human, score)
    check("plain quantise removes the lag", abs(flat_error) < 0.01, f"{flat_error:.4f}")
    check("learned offset keeps the lag", 0.02 < human_error < 0.08, f"{human_error:.4f}")
    check("human version is still on the grid", _all_on_grid(human, 100.0, 2, offset=profile.global_timing.mean))


def test_pitch_correction() -> None:
    print("\n[6] Pitch correction")
    score = build_score()
    take = human_take(score, wrong_note_chance=0.5, missed_note_chance=0.0)
    alignment = align_sequences(score, take)

    fixed, report = correct_pitches(alignment, score, take)
    check("some pitches were corrected", len(report.pitch_fixed) > 0, f"{len(report.pitch_fixed)}")

    wrong_before = sum(
        1 for r, p in alignment.pairs
        if score.strikes()[r].pitch != take.strikes()[p].pitch
    )
    wrong_after = 0
    for r, p in alignment.pairs:
        note = fixed.notes[p]
        if note.pitch != score.strikes()[r].pitch:
            wrong_after += 1
    check("wrong notes were repaired", wrong_after < wrong_before,
          f"{wrong_before} -> {wrong_after}")


def test_key_detection() -> None:
    print("\n[7] Key detection")
    score = build_score()
    key = key_of(score)
    check("C major detected", key is not None and "C" in key, f"got {key}")


def test_full_pass() -> None:
    print("\n[8] Full correction pass")
    score = build_score()
    take = human_take(score, mean_lag=0.045, lag_spread=0.025)
    grid = Grid(TempoMap.constant(100.0), divisions=2)

    profile = PlayerProfile()
    alignment = align_sequences(score, take)
    profile.observe(alignment, score, take)

    corrected, report = correct_performance(
        score, take, grid, profile=profile, quantise_strength=1.0, fix_pitches=True
    )
    check("correction produced output", len(corrected) > 0)
    check("report has content", "quantised" in report.summary())

    raw_error = _mean_abs_error(take, score)
    corrected_error = _mean_abs_error(corrected, score)
    check("full pass improves accuracy", corrected_error < raw_error,
          f"{raw_error:.4f} -> {corrected_error:.4f}")

    print("      " + report.summary().replace("\n", "\n      "))


def test_strict_mode() -> None:
    print("\n[9] Strict quantisation")
    score = build_score()
    take = human_take(score, mean_lag=0.045, lag_spread=0.030)
    grid = Grid(TempoMap.constant(100.0), divisions=2)

    profile = PlayerProfile()
    alignment = align_sequences(score, take)
    profile.observe(alignment, score, take)

    strict, _ = correct_performance(
        score, take, grid, profile=profile, quantise_strength=1.0, strict=True
    )
    check("strict output is on the grid", _all_on_grid(strict, 100.0, 2),
          f"max dev {max(abs((n.start/0.3)-round(n.start/0.3)) for n in strict.strikes()):.4f} steps")
    strict_error = _mean_signed_error(strict, score)
    check("strict output is accurate to the score", abs(strict_error) < 0.01,
          f"{strict_error*1000:.1f} ms")

    # The humanised version must keep the lag; the strict one must not.
    human, _ = correct_performance(
        score, take, grid, profile=profile, quantise_strength=1.0, strict=False
    )
    human_error = _mean_signed_error(human, score)
    check("strict removes the lag that humanised keeps",
          abs(strict_error) < abs(human_error),
          f"strict {strict_error*1000:.1f} ms vs humanised {human_error*1000:.1f} ms")


def test_global_offset() -> None:
    print("\n[10] Global offset detection")
    score = build_score()
    take = human_take(score, mean_lag=0.0, lag_spread=0.010)
    shifted = Sequence(
        notes=[n.shifted(2.4) for n in take.notes], tempo_map=list(take.tempo_map)
    )

    offset, matches = find_global_offset(score, shifted)
    check("offset recovered", abs(offset - 2.4) < 0.08, f"got {offset:+.3f}s")
    check("most notes matched at that offset", matches > 0.8 * len(score.strikes()),
          f"{matches} of {len(score.strikes())}")

    corrected_take = Sequence(
        notes=[n.shifted(-offset) for n in shifted.notes], tempo_map=list(shifted.tempo_map)
    )
    alignment = align_sequences(score, corrected_take)
    check("alignment works after offsetting", alignment.coverage > 0.85,
          f"coverage {alignment.coverage:.1%}")


def test_octave_shift() -> None:
    print("\n[11] Octave shift detection")
    score = build_score()
    take = human_take(score, mean_lag=0.02)

    # The player follows the guide an octave down.
    down = Sequence(
        notes=[n.with_pitch(n.pitch - 12) for n in take.notes],
        tempo_map=list(take.tempo_map),
    )

    alignment = align_sequences(score, down)
    check("alignment survives an octave transposition", alignment.coverage > 0.8,
          f"coverage {alignment.coverage:.1%}")

    shift = detect_octave_shift(score, down, alignment)
    check("octave down detected", shift == -1, f"got {shift}")

    # The player follows the guide an octave UP.
    back = Sequence(
        notes=[n.with_pitch(n.pitch + 12) for n in take.notes],
        tempo_map=list(take.tempo_map),
    )
    check("octave up detected", detect_octave_shift(score, back) == 1)

    # No shift when the player stays in register.
    check("no shift detected when in register", detect_octave_shift(score, take) == 0)


def test_transposed_take_is_corrected() -> None:
    print("\n[12] Correcting a transposed take")
    score = build_score()
    take = human_take(score, mean_lag=0.03)
    down = Sequence(
        notes=[n.with_pitch(n.pitch - 12) for n in take.notes],
        tempo_map=list(take.tempo_map),
    )
    grid = Grid(TempoMap.constant(100.0), divisions=2)

    corrected, report = correct_performance(score, down, grid, strict=True)

    wrong_after = 0
    alignment = align_sequences(score, corrected)
    for ref_index, perf_index in alignment.pairs:
        if score.strikes()[ref_index].pitch != corrected.strikes()[perf_index].pitch:
            wrong_after += 1
    check("transposed take is transposed back", wrong_after == 0, f"{wrong_after} wrong pitches")
    check("the shift is reported", any("transposed" in note for note in report.notes))


def _mean_abs_error(seq: Sequence, score: Sequence) -> float:
    alignment = align_sequences(score, seq)
    ref = score.strikes()
    perf = seq.strikes()
    errors = [
        abs(perf[p].start - ref[r].start)
        for r, p in alignment.pairs
        if r < len(ref) and p < len(perf)
    ]
    return sum(errors) / len(errors) if errors else 0.0


def _mean_signed_error(seq: Sequence, score: Sequence) -> float:
    alignment = align_sequences(score, seq)
    ref = score.strikes()
    perf = seq.strikes()
    errors = [
        perf[p].start - ref[r].start
        for r, p in alignment.pairs
        if r < len(ref) and p < len(perf)
    ]
    return sum(errors) / len(errors) if errors else 0.0


def _all_on_grid(seq: Sequence, bpm: float, divisions: int, offset: float = 0.0) -> bool:
    """Every onset sits on the grid (optionally shifted by `offset`)."""
    step = 60.0 / bpm / divisions
    for note in seq.strikes():
        steps = (note.start - offset) / step
        if abs(steps - round(steps)) > 1e-3:
            return False
    return True


def main() -> int:
    print("=" * 68)
    print("Piano accompaniment pipeline - end-to-end test")
    print("=" * 68)

    test_midi_roundtrip()
    test_alignment()
    test_profile_learning()
    test_quantise()
    test_quantise_keeps_human_offset()
    test_pitch_correction()
    test_key_detection()
    test_full_pass()
    test_strict_mode()
    test_global_offset()
    test_octave_shift()
    test_transposed_take_is_corrected()

    print("\n" + "=" * 68)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  FAILED: {name}")
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
