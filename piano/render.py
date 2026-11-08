"""Render a MIDI file through sfizz and report on the audio.

Usage:
    python3 -m piano.render song.mid [out.wav] [--sfz path]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from piano.io.audio import (
    DEFAULT_SFZ,
    available_engines,
    peak_normalize,
    render,
    rms,
)
from piano.io.midi_io import read_midi


def main() -> int:
    parser = argparse.ArgumentParser(description="Render MIDI through sfizz")
    parser.add_argument("midi", help="input MIDI file")
    parser.add_argument("wav", nargs="?", help="output WAV file")
    parser.add_argument("--sfz", default=None, help="SFZ instrument (default: Salamander)")
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--no-normalize", action="store_true", help="skip peak normalisation")
    args = parser.parse_args()

    if not os.path.isfile(args.midi):
        print(f"error: no such MIDI file: {args.midi}", file=sys.stderr)
        return 1

    wav_path = args.wav or os.path.splitext(args.midi)[0] + ".wav"

    print(f"engines available : {', '.join(available_engines()) or 'none'}")
    print(f"instrument        : {args.sfz or DEFAULT_SFZ}")

    sequence = read_midi(args.midi)
    print(f"midi              : {len(sequence)} notes, {sequence.duration():.2f}s, "
          f"{len(sequence.tempo_map)} tempo events")

    result = render(args.midi, wav_path, args.sfz, args.sample_rate)

    audio = result.audio
    if not args.no_normalize:
        audio = peak_normalize(audio)

    print(f"rendered          : {result.duration:.2f}s @ {result.sample_rate} Hz")
    print(f"peak              : {result.peak():.4f}")
    print(f"rms               : {rms(audio):.6f}")
    print(f"wav               : {result.wav_path}")

    if result.duration > 0 and rms(audio) < 1e-5:
        print("WARNING: rendered audio is silent", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
