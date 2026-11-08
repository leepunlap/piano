"""Raw MIDI capture to a file, using rtmidi directly.

Used to verify that a keyboard is actually delivering notes, and as a
diagnostic when the higher-level monitor shows nothing.

Usage:
    python3 -m piano.io.rawcapture <port-name-or-index> <output.jsonl> [seconds]
"""

from __future__ import annotations

import json
import os
import sys
import time

import rtmidi


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    target = sys.argv[1]
    out_path = sys.argv[2]
    limit = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0

    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()
    if not ports:
        print("no MIDI input ports", file=sys.stderr)
        return 1

    if target.isdigit():
        index = int(target)
    else:
        matches = [i for i, name in enumerate(ports) if name == target]
        if not matches:
            matches = [i for i, name in enumerate(ports) if target.lower() in name.lower()]
        if not matches:
            print(f"port not found: {target} (have {ports})", file=sys.stderr)
            return 1
        index = matches[0]

    print(f"capturing from [{index}] {ports[index]} -> {out_path}", flush=True)

    events: list[tdict] = []  # type: ignore[name-defined]
    start = time.monotonic()

    def callback(message, data=None):
        events.append(
            {
                "t": round(time.monotonic() - start, 6),
                "raw": list(message[0]) if isinstance(message, (list, tuple)) else message,
                "delta": message[1] if isinstance(message, (list, tuple)) and len(message) > 1 else None,
            }
        )

    midi_in.open_port(index)
    midi_in.set_callback(callback)

    try:
        while True:
            time.sleep(0.2)
            with open(out_path, "w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event) + "\n")
            elapsed = time.monotonic() - start
            print(f"\r  {elapsed:6.1f}s  {len(events)} messages", end="", flush=True)
            if limit and elapsed >= limit:
                break
    except KeyboardInterrupt:
        pass
    finally:
        print()
        print(f"total messages: {len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
