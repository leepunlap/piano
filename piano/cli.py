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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from piano.core.align import align_sequences, key_of
from piano.core.correct import correct_performance
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
    if not ports:
        print("no MIDI input ports found")
        return 1
    for port in ports:
        print(port)
    return 0


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

    p_demo = sub.add_parser("demo", help="run the self-test")
    p_demo.set_defaults(func=cmd_demo)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
