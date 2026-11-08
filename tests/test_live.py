"""Deterministic test of the live MIDI capture path using virtual ports.

The real keyboard is only one possible source of MIDI.  This test wires a
virtual output to a virtual input and checks that the whole capture path -
mido open, callback, note pairing, timing - works, without needing anyone to
play anything.

Usage:
    python3 -m tests.test_live
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name} {detail}")


def _virtual_ports() -> tuple[object, object]:
    """Create a virtual output and a virtual input, cross-connected."""
    import rtmidi

    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()
    midi_in.open_virtual_port("PianoLoopIn")
    midi_out.open_virtual_port("PianoLoopOut")
    return midi_in, midi_out


def test_virtual_ports_exist() -> None:
    print("\n[1] Virtual MIDI ports")
    try:
        midi_in, midi_out = _virtual_ports()
    except Exception as exc:
        check("virtual ports can be created", False, str(exc))
        return

    check("virtual ports can be created", True)
    check("virtual output is visible as an input", "PianoLoopOut" in midi_in.get_ports())
    check("virtual input is visible as an output", "PianoLoopIn" in midi_out.get_ports())

    midi_in.delete()
    midi_out.delete()


def test_capture_path() -> None:
    print("\n[2] Capture path (virtual loopback -> mido)")
    import mido
    from piano.io.live import MidiMonitor

    midi_in, midi_out = _virtual_ports()
    time.sleep(0.3)

    try:
        # mido should see the virtual output port as an input.
        names = mido.get_input_names()
        check("mido sees the virtual port", "PianoLoopOut" in names, f"{names}")

        if "PianoLoopOut" not in names:
            return

        monitor = MidiMonitor("PianoLoopOut")
        received: list = []

        with monitor:
            # Send a short phrase through the virtual output.
            sender = mido.open_output("PianoLoopIn")
            time.sleep(0.2)

            notes = [(60, 0.0), (64, 0.25), (67, 0.5), (72, 0.75)]
            for pitch, offset in notes:
                sender.send(mido.Message("note_on", note=pitch, velocity=90, time=0))
                time.sleep(0.02)
                sender.send(mido.Message("note_off", note=pitch, velocity=0, time=0))
                time.sleep(max(0.0, offset - 0.02) if offset else 0.2)

            time.sleep(0.4)
            sender.close()

        captured = monitor.notes()
        received.extend(captured)

        check("notes were captured", len(captured) == len(notes), f"got {len(captured)}")
        check("pitches are correct", [n.pitch for n in captured] == [p for p, _ in notes],
              f"{[n.pitch for n in captured]}")
        check("velocities are correct", all(n.velocity == 90 for n in captured))
        check("onsets increase", all(
            captured[i].start < captured[i + 1].start for i in range(len(captured) - 1)))
        check("durations are sane", all(0.01 < n.duration < 1.0 for n in captured),
              f"{[round(n.duration, 3) for n in captured]}")

        if len(captured) >= 2:
            gap = captured[1].start - captured[0].start
            check("inter-onset gap is plausible (0.2-0.4s)", 0.15 < gap < 0.45, f"{gap:.3f}s")

        summary = monitor.summary()
        check("summary counts note events", summary.get("note_on", 0) >= len(notes), f"{summary}")
    finally:
        midi_in.delete()
        midi_out.delete()


def test_monitor_describes_messages() -> None:
    print("\n[3] Message descriptions")
    import mido
    from piano.io.live import MonitorEvent

    events = [
        MonitorEvent(0.0, mido.Message("note_on", note=60, velocity=90, time=0)),
        MonitorEvent(0.1, mido.Message("note_off", note=60, velocity=0, time=0)),
        MonitorEvent(0.2, mido.Message("control_change", control=64, value=127, time=0)),
        MonitorEvent(0.3, mido.MetaMessage("end_of_track", time=0)),
    ]
    descriptions = [event.describe() for event in events]
    check("note_on described", "note_on" in descriptions[0] and "60" in descriptions[0])
    check("note_off described", "note_off" in descriptions[1])
    check("control change described", "control_change" in descriptions[2])
    check("meta message described", "meta" in descriptions[3])


def test_practice_session_records() -> None:
    print("\n[4] Practice session records what is played")
    import mido
    from piano.core.notes import Note, Sequence
    from piano.io.live import MidiSession

    midi_in, midi_out = _virtual_ports()
    time.sleep(0.3)

    try:
        reference = Sequence(
            notes=[
                Note(60, 0.0, 0.5, 80),
                Note(62, 0.5, 1.0, 80),
                Note(64, 1.0, 1.5, 80),
            ],
            tempo_map=[(0.0, 120.0)],
            name="ref",
        )

        session = MidiSession(
            input_port="PianoLoopOut",
            output_port="PianoLoopIn",
            send_reference=False,
        )

        # Play the "player" notes on a separate thread so they arrive while
        # the session is recording.
        def play_along():
            time.sleep(0.6)
            sender = mido.open_output("PianoLoopIn")
            time.sleep(0.2)
            for pitch, offset in [(60, 0.0), (62, 0.55), (64, 1.08)]:
                sender.send(mido.Message("note_on", note=pitch, velocity=85, time=0))
                time.sleep(0.03)
                sender.send(mido.Message("note_off", note=pitch, velocity=0, time=0))
                time.sleep(0.5 if offset == 0.0 else 0.5)
            sender.close()

        thread = threading.Thread(target=play_along, daemon=True)
        thread.start()

        ref, take = session.play_and_record(reference, duration=2.5, count_in_beats=0)
        thread.join(timeout=3)

        check("session captured the played notes", len(take) >= 3, f"got {len(take)}")
        if take.notes:
            check("captured pitches match what was played",
                  [n.pitch for n in take.strikes()] == [60, 62, 64],
                  f"{[n.pitch for n in take.strikes()]}")
            check("take keeps the reference tempo map", len(take.tempo_map) > 0)
    finally:
        midi_in.delete()
        midi_out.delete()


def main() -> int:
    print("=" * 68)
    print("Live MIDI capture tests (virtual loopback)")
    print("=" * 68)

    test_virtual_ports_exist()
    test_capture_path()
    test_monitor_describes_messages()
    test_practice_session_records()

    print("\n" + "=" * 68)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
