"""Command-line front end for the accompaniment system.

Subcommands:

    render    render a MIDI file through sfizz
    learn     align a performance against a score and update the player profile
    correct   quantise/correct a performance against a score
    profile   show what the system has learned about a player
    ports     list available MIDI input ports
    demo      run the whole pipeline on a synthetic example
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from piano.core.align import align_sequences, detect_octave_shift, find_global_offset, key_of
from piano.core.correct import correct_performance, transpose
from piano.core.notes import Sequence
from piano.core.profile import PlayerProfile, profile_path
from piano.core.tempo import Grid, TempoMap
from piano.io.audio import DEFAULT_SFZ, available_engines, render
from piano.io.midi_io import read_midi, write_midi_with_tempo_map
from piano.io.recorder import list_ports

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(REPO_ROOT, "profiles")


def cmd_render(args: argparse.Namespace) -> int:
    wav = args.wav or os.path.splitext(args.midi)[0] + ".wav"
    print(f"engines: {', '.join(available_engines()) or 'none'}")
    result = render(args.midi, wav, args.sfz, args.sample_rate)
    print(f"{result.duration:.2f}s @ {result.sample_rate} Hz -> {result.wav_path}")
    print(f"peak {result.peak():.4f}")
    return 0


def _load(args: argparse.Namespace) -> tuple[Sequence, Sequence]:
    score = read_midi(args.score)
    take = read_midi(args.take)
    return score, take


def cmd_learn(args: argparse.Namespace) -> int:
    score, take = _load(args)
    alignment = align_sequences(score, take)

    path = profile_path(PROFILE_DIR, args.player, args.song or score.name)
    profile = PlayerProfile.load(path) if os.path.isfile(path) else PlayerProfile(
        player_id=args.player, song_id=args.song or score.name
    )

    contributed = profile.observe(alignment, score, take)
    profile.save(path)

    print(f"matched {contributed} notes ({alignment.coverage:.1%} of the score)")
    print(f"profile: {path}")
    print()
    print(profile.summary())
    return 0


def cmd_correct(args: argparse.Namespace) -> int:
    score, take = _load(args)

    path = profile_path(PROFILE_DIR, args.player, args.song or score.name)
    profile = None
    if os.path.isfile(path) and not args.no_profile:
        profile = PlayerProfile.load(path)

    divisions = {"quarter": 1, "eighth": 2, "sixteenth": 4, "thirtysecond": 8}[args.division]
    bpm = score.tempo_map[0][1] if score.tempo_map else 120.0
    grid = Grid(TempoMap.constant(bpm), divisions=divisions)

    corrected, report = correct_performance(
        score,
        take,
        grid,
        profile=profile,
        quantise_strength=args.strength,
        fix_pitches=not args.no_pitch_fix,
        add_missing=args.add_missing,
        velocity_blend=1.0 if profile else 0.0,
        strict=args.strict,
    )

    out = args.out or os.path.splitext(args.take)[0] + ".corrected.mid"
    write_midi_with_tempo_map(corrected, out)

    print(report.summary())
    print()
    print(f"corrected MIDI -> {out}")
    if profile is None:
        print("note: no player profile found; timing offset was not preserved")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    path = profile_path(PROFILE_DIR, args.player, args.song or "")
    if not os.path.isfile(path):
        print(f"no profile at {path}")
        return 1
    profile = PlayerProfile.load(path)
    print(f"player: {profile.player_id}   song: {profile.song_id or '(any)'}")
    print()
    print(profile.summary())
    return 0


def cmd_ports(args: argparse.Namespace) -> int:
    ports = list_ports()
    outputs = []
    try:
        import mido

        outputs = sorted(mido.get_output_names())
    except Exception:
        pass

    print("MIDI inputs:")
    if ports:
        for port in ports:
            print(f"  {port}")
    else:
        print("  (none)")

    print("MIDI outputs:")
    if outputs:
        for port in outputs:
            print(f"  {port}")
    else:
        print("  (none)")
    return 0 if ports else 1


def cmd_monitor(args: argparse.Namespace) -> int:
    """Watch a MIDI input and print what arrives."""
    from piano.io.live import MidiMonitor

    ports = list_ports()
    port = args.port or (ports[0] if ports else None)
    if port is None:
        print("no MIDI input ports found", file=sys.stderr)
        return 1

    print(f"listening on: {port}")
    print("press keys on the keyboard; Ctrl-C to stop\n")

    with MidiMonitor(port) as monitor:
        try:
            while True:
                time.sleep(0.1)
                count = len(monitor.events)
                if count > getattr(monitor, "_printed", 0):
                    for event in monitor.events[getattr(monitor, "_printed", 0):]:
                        print(f"  {event.time:7.3f}s  {event.describe()}")
                    monitor._printed = count
        except KeyboardInterrupt:
            print()

    print()
    print("summary:", monitor.summary())
    notes = monitor.notes()
    print(f"notes captured: {len(notes)}")
    if notes:
        pitches = sorted({n.pitch for n in notes})
        print(f"distinct pitches: {pitches}")
        print(f"first note: {notes[0]}")
    return 0


def cmd_practice(args: argparse.Namespace) -> int:
    """Play a reference song, record the player's take, then correct it."""
    from piano.io.keyboard import CaptureConfig, KeyboardCapture

    score = read_midi(args.score)

    capture = KeyboardCapture(port=args.input, config=CaptureConfig(
        count_in_beats=args.count_in,
        count_in_bpm=score.tempo_map[0][1] if score.tempo_map else 100.0,
        lead_in=args.lead_in,
        trailing=args.trailing,
        send_reference=args.send_reference,
        reference_port=args.output,
        play_guide_audio=not args.no_guide,
        guide_sfz=args.sfz,
        guide_device=args.audio_device,
    ))

    port = capture.resolve_port(args.port_keyword)
    if port is None:
        print("no MIDI input ports found", file=sys.stderr)
        return 1

    print(f"score       : {args.score} ({len(score)} notes, {score.duration():.1f}s)")
    print(f"input port  : {port}")
    if not args.no_guide:
        print(f"guide track : local audio ({args.audio_device or 'default device'})")
    if args.send_reference:
        print(f"midi out    : {args.output or '(default)'}")
    print()
    print("Play along! Ctrl-C to stop early.")
    print()

    try:
        result = capture.capture(
            reference=score,
            duration=args.duration,
            on_progress=_progress_printer(),
        )
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130

    print()
    print(f"captured    : {result.summary()}")

    take = result.sequence
    if len(take) == 0:
        print("nothing was played - check the input port", file=sys.stderr)
        return 2

    # Always keep the raw take so it can be inspected.
    raw_path = args.out or os.path.splitext(args.score)[0] + ".corrected.mid"
    raw_path = os.path.splitext(raw_path)[0] + ".take.mid"
    write_midi_with_tempo_map(take, raw_path)
    print(f"raw take     -> {raw_path}")

    # The player may have started late or early relative to the guide, so
    # find the offset that best lines the take up with the score.
    offset, coarse_matches = find_global_offset(score, take)
    if abs(offset) > 0.05:
        take = Sequence(
            notes=[n.shifted(offset) for n in take.notes],
            tempo_map=list(take.tempo_map),
            name=take.name,
        )
        print(f"start offset  : {offset:+.2f}s (line-up with the score)")

    # Learn from this take.
    alignment = align_sequences(score, take)

    # The player may have followed the guide in another octave.
    octave_shift = detect_octave_shift(score, take, alignment)
    if octave_shift:
        print(f"octave shift  : {octave_shift:+d} (transposing back into the score's register)")
        take = transpose(take, -12 * octave_shift)
        alignment = align_sequences(score, take)
    path = profile_path(PROFILE_DIR, args.player, args.song or score.name)
    profile = PlayerProfile.load(path) if os.path.isfile(path) else PlayerProfile(
        player_id=args.player, song_id=args.song or score.name
    )
    profile.observe(alignment, score, take)
    profile.save(path)

    print()
    print(f"alignment   : {alignment.coverage:.1%} of the score matched")
    print(profile.summary())

    # Correct it.
    divisions = {"quarter": 1, "eighth": 2, "sixteenth": 4, "thirtysecond": 8}[args.division]
    bpm = score.tempo_map[0][1] if score.tempo_map else 120.0
    grid = Grid(TempoMap.constant(bpm), divisions=divisions)

    corrected, report = correct_performance(
        score,
        take,
        grid,
        profile=profile,
        quantise_strength=args.strength,
        strict=args.strict,
        octave_shift=0,  # already applied above
    )

    out = args.out or os.path.splitext(args.score)[0] + ".corrected.mid"
    write_midi_with_tempo_map(corrected, out)

    print()
    print(report.summary())
    print()
    print(f"corrected MIDI -> {out}")

    if args.render:
        wav = os.path.splitext(out)[0] + ".wav"
        rendered = render(out, wav, args.sfz)
        print(f"rendered      -> {rendered.wav_path} ({rendered.duration:.1f}s)")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Record a take with no reference - just capture what is played."""
    from piano.io.keyboard import CaptureConfig, KeyboardCapture

    capture = KeyboardCapture(port=args.input, config=CaptureConfig(
        count_in_beats=args.count_in,
        count_in_bpm=args.bpm,
        trailing=0.0,
        send_reference=False,
    ))

    port = capture.resolve_port(args.port_keyword)
    if port is None:
        print("no MIDI input ports found", file=sys.stderr)
        return 1

    print(f"recording from: {port}")
    print(f"duration      : {args.duration}s")
    print()
    try:
        result = capture.capture(duration=args.duration, on_progress=_progress_printer())
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130

    print()
    print(result.summary())
    out = args.out or os.path.join("work", "take.mid")
    write_midi_with_tempo_map(result.sequence, out)
    print(f"MIDI -> {out}")
    return 0


def _progress_printer():
    state = {"last": -1.0}

    def report(elapsed: float, notes: int) -> None:
        if elapsed - state["last"] < 0.5:
            return
        state["last"] = elapsed
        bar = int(elapsed * 4) % 4
        print(f"\r  {elapsed:5.1f}s  {'*' * bar}{' ' * (4 - bar)}  {notes} notes   ", end="")

    return report


def cmd_demo(args: argparse.Namespace) -> int:
    from tests.test_pipeline import main as run_tests

    return run_tests()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="piano", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="render a MIDI file through sfizz")
    p_render.add_argument("midi")
    p_render.add_argument("wav", nargs="?")
    p_render.add_argument("--sfz", default=None)
    p_render.add_argument("--sample-rate", type=int, default=48000)
    p_render.set_defaults(func=cmd_render)

    p_learn = sub.add_parser("learn", help="learn from a performance")
    p_learn.add_argument("score", help="reference MIDI")
    p_learn.add_argument("take", help="your performance MIDI")
    p_learn.add_argument("--player", default="default")
    p_learn.add_argument("--song", default=None)
    p_learn.set_defaults(func=cmd_learn)

    p_correct = sub.add_parser("correct", help="quantise/correct a performance")
    p_correct.add_argument("score", help="reference MIDI")
    p_correct.add_argument("take", help="your performance MIDI")
    p_correct.add_argument("--out", default=None)
    p_correct.add_argument("--player", default="default")
    p_correct.add_argument("--song", default=None)
    p_correct.add_argument(
        "--division", default="sixteenth", choices=["quarter", "eighth", "sixteenth", "thirtysecond"]
    )
    p_correct.add_argument("--strength", type=float, default=1.0, help="0 = untouched, 1 = fully quantised")
    p_correct.add_argument("--no-pitch-fix", action="store_true")
    p_correct.add_argument("--add-missing", action="store_true")
    p_correct.add_argument("--no-profile", action="store_true")
    p_correct.add_argument(
        "--strict",
        action="store_true",
        help="snap exactly onto the grid (default: keep the player's learned feel)",
    )
    p_correct.set_defaults(func=cmd_correct)

    p_profile = sub.add_parser("profile", help="show a learned profile")
    p_profile.add_argument("--player", default="default")
    p_profile.add_argument("--song", default=None)
    p_profile.set_defaults(func=cmd_profile)

    p_ports = sub.add_parser("ports", help="list MIDI input ports")
    p_ports.set_defaults(func=cmd_ports)

    p_monitor = sub.add_parser("monitor", help="watch a MIDI input live")
    p_monitor.add_argument("--port", default=None, help="port name (default: first available)")
    p_monitor.set_defaults(func=cmd_monitor)

    p_practice = sub.add_parser("practice", help="play along with a song, then correct it")
    p_practice.add_argument("score", help="reference MIDI to play along with")
    p_practice.add_argument("--input", default=None, help="MIDI input port")
    p_practice.add_argument("--output", default=None, help="MIDI output port for the reference")
    p_practice.add_argument("--port-keyword", default="", help="substring to pick a port automatically")
    p_practice.add_argument("--player", default="default")
    p_practice.add_argument("--song", default=None)
    p_practice.add_argument("--division", default="eighth", choices=["quarter", "eighth", "sixteenth", "thirtysecond"])
    p_practice.add_argument("--strength", type=float, default=1.0)
    p_practice.add_argument("--strict", action="store_true")
    p_practice.add_argument("--duration", type=float, default=None, help="seconds (default: song length + trailing)")
    p_practice.add_argument("--count-in", type=int, default=4, help="count-in beats")
    p_practice.add_argument("--lead-in", type=float, default=0.0, help="extra seconds before the reference starts")
    p_practice.add_argument("--trailing", type=float, default=2.0, help="extra seconds recorded after the reference ends")
    p_practice.add_argument("--no-reference", action="store_true", help="(legacy) do not send the reference out")
    p_practice.add_argument("--no-guide", action="store_true", help="do not play the local guide track")
    p_practice.add_argument("--send-reference", action="store_true",
                             help="also send the reference to the keyboard as MIDI")
    p_practice.add_argument("--audio-device", default=None, help="audio output device for the guide track")
    p_practice.add_argument("--render", action="store_true", help="also render the corrected MIDI to WAV")
    p_practice.add_argument("--sfz", default=None)
    p_practice.add_argument("--out", default=None)
    p_practice.set_defaults(func=cmd_practice)

    p_record = sub.add_parser("record", help="record a take with no reference")
    p_record.add_argument("--input", default=None, help="MIDI input port")
    p_record.add_argument("--port-keyword", default="", help="substring to pick a port automatically")
    p_record.add_argument("--duration", type=float, default=10.0, help="seconds")
    p_record.add_argument("--count-in", type=int, default=0)
    p_record.add_argument("--bpm", type=float, default=100.0, help="count-in tempo")
    p_record.add_argument("--out", default=None)
    p_record.set_defaults(func=cmd_record)

    p_demo = sub.add_parser("demo", help="run the self-test")
    p_demo.set_defaults(func=cmd_demo)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
