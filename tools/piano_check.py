#!/usr/bin/env python3
"""Check whether the piano on the desk is making a sound.

MIDI gives no feedback: a message is handed to the instrument and forgotten,
so "the notes were sent" and "the piano played" are entirely different claims
and only one of them can be checked from the software.  The other one needs an
ear, and there is a microphone on the desk.

So this listens, and it separates the three things that could be wrong:

  * the instrument makes no sound when its keys are pressed — the sound
    engine is off, or the volume is down, or headphones have muted the
    speakers;
  * the instrument plays when its keys are pressed but not when notes are
    sent to it — the sound works and the MIDI in does not;
  * the instrument plays neither — then the MIDI path is broken as well and
    there is nothing to send.

The recording is made in three parts: the room on its own, the notes sent to
the instrument, and the keys played by hand.  Each part is measured against
the room, and each is also looked at in the frequency domain, because a piano
is not noise: it has a fundamental and a stack of harmonics above it.

Usage:
    piano_check.py --listen          measure the room, the MIDI, and the keys
    piano_check.py --send-only       just send notes to the instrument
"""
from __future__ import annotations

import argparse
import sys
import threading
import time

import mido
import numpy as np
import sounddevice as sd

RATE = 48000


def find_microphone() -> tuple[int, str]:
    devices = sd.query_devices()
    candidates = [
        (index, device["name"])
        for index, device in enumerate(devices)
        if device["max_input_channels"] > 0
    ]
    preferred = [
        c for c in candidates if "microphone" in c[1].lower() and "macbook" not in c[1].lower()
    ]
    if not preferred:
        raise SystemExit("no microphone to listen with")
    return preferred[0]


def record(seconds: float, device: int) -> np.ndarray:
    frames = int(seconds * RATE)
    buffer = np.zeros(frames, dtype="float32")
    with sd.InputStream(device=device, samplerate=RATE, channels=1, dtype="float32") as stream:
        written = 0
        while written < frames:
            data, _ = stream.read(frames - written)
            if data is None:
                break
            buffer[written : written + len(data)] = data.reshape(-1)
            written += len(data)
    return buffer


def rms(data: np.ndarray) -> float:
    if data.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(data))))


def spectrum(data: np.ndarray) -> np.ndarray:
    """The average magnitude spectrum, so a piano can be told from a hiss."""
    if data.size < 1024:
        return np.zeros(0)
    window = np.hanning(min(8192, data.size))
    piece = data[: min(8192, data.size)] * window
    return np.abs(np.fft.rfft(piece))


def describe(data: np.ndarray) -> str:
    if data.size == 0:
        return "nothing"
    spectrum_data = spectrum(data)
    if spectrum_data.size == 0:
        return "nothing"
    frequencies = np.fft.rfftfreq(min(8192, data.size), 1 / RATE)
    peak = int(np.argmax(spectrum_data))
    total = float(np.sum(spectrum_data)) or 1.0
    # How much of the energy is at one pitch rather than spread around: a
    # piano note has a clear fundamental.
    concentration = float(spectrum_data[peak]) / total
    return (
        f"loudest {frequencies[peak]:7.1f} Hz, "
        f"concentration {concentration:.3f}, "
        f"energy above 2 kHz {float(np.sum(spectrum_data[frequencies > 2000])) / total:.3f}"
    )


def send_notes(port: str, seconds: float, channels=(0, 1)) -> None:
    """A chord and then a run of notes, on the given channels."""
    out = mido.open_output(port)
    try:
        for channel in channels:
            out.send(mido.Message("program_change", program=0, channel=channel))
            for note in (60, 64, 67, 72):
                out.send(mido.Message("note_on", note=note, velocity=100, channel=channel))
        time.sleep(1.5)
        for channel in channels:
            for note in (60, 64, 67, 72):
                out.send(mido.Message("note_off", note=note, velocity=0, channel=channel))
        time.sleep(0.3)
        for step, note in enumerate((60, 62, 64, 65, 67, 69)):
            out.send(mido.Message("note_on", note=note, velocity=100, channel=0))
            time.sleep(0.22)
            out.send(mido.Message("note_off", note=note, velocity=0, channel=0))
        time.sleep(0.3)
    finally:
        out.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", action="store_true", help="run the full three-part test")
    ap.add_argument("--send-only", action="store_true")
    ap.add_argument("--port", help="which MIDI output to use")
    args = ap.parse_args()

    device, name = find_microphone()
    print(f"listening through: {name}")

    if args.send_only:
        ports = [args.port] if args.port else mido.get_output_names()
        for port in ports:
            print(f"sending to {port}")
            send_notes(port, 4.0)
        return 0

    ports = [args.port] if args.port else mido.get_output_names()
    timeline = [
        ("room", 6.0, None),
        ("midi", 8.0, ports[0] if ports else None),
        ("room again", 4.0, None),
        ("keys", 18.0, None),
    ]
    total = sum(duration for _, duration, _ in timeline)
    print(f"recording {total:.0f} seconds: {total:.0f}s of it is for you to play the keys")
    print("the notes are sent to the instrument about 6 seconds in")

    captured: dict[str, np.ndarray] = {}

    def listen() -> None:
        chunks = []
        for label, duration, _ in timeline:
            chunks.append((label, record(duration, device)))
        captured["chunks"] = chunks  # type: ignore[assignment]

    listener = threading.Thread(target=listen)
    listener.start()
    time.sleep(6.5)
    for port in ports:
        print(f"  sending notes to {port}")
        try:
            send_notes(port, 4.0)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not send to {port}: {exc}")
    listener.join()

    chunks = captured.get("chunks", [])
    baseline = 0.0
    for label, data in chunks:
        if label.startswith("room"):
            baseline = max(baseline, rms(data))
    print(f"\nroom level: {baseline:.5f}")
    for label, data in chunks:
        level = rms(data)
        ratio = level / baseline if baseline > 0 else float("inf")
        print(f"  {label:12s} rms {level:.5f}  ({ratio:5.1f}x the room)  {describe(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
