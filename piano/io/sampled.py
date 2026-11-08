"""Play the sample library live, from a MIDI keyboard.

The guide tracks are rendered offline, which is no use when the player wants
to *play* rather than to follow.  This is the same instrument, running live:
sfizz is loaded as a library, the notes coming in from the keyboard are given
to it, and the sound it makes comes out of the speakers.

The point is the sound.  A MIDI keyboard's own sound module is a synthesiser
approximating a piano; a sampled library is an actual recorded grand, with
sixteen velocity layers of it, so a soft note and a hard note are different
recordings rather than the same recording played at different volumes.

The Nord Electro on the desk sends no MIDI at the moment -- its MIDI Out is
switched off in its own setup -- so nothing will come in until that is
turned back on.  Everything else here works.
"""
from __future__ import annotations

import ctypes
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SFIZZ_LIBRARY = REPO_ROOT / "bin" / "lib" / "libsfizz.dylib"
DEFAULT_SFZ = (
    REPO_ROOT / "samples" / "SalamanderGrandPiano" / "Salamander Grand Piano V3.sfz"
)


class SampledPiano:
    """The sample library, playable in real time."""

    def __init__(
        self,
        sfz_path: Optional[Path] = None,
        sample_rate: int = 48000,
        block_size: int = 256,
        voices: int = 64,
    ) -> None:
        if not SFIZZ_LIBRARY.exists():
            raise FileNotFoundError(
                f"libsfizz not found at {SFIZZ_LIBRARY}. "
                "Download it from https://github.com/sfztools/sfizz/releases"
            )
        self.sfz_path = Path(sfz_path or DEFAULT_SFZ)
        if not self.sfz_path.exists():
            raise FileNotFoundError(f"no such sample library: {self.sfz_path}")

        self.sample_rate = sample_rate
        self.block_size = block_size
        self._lock = threading.Lock()

        self._lib = ctypes.CDLL(str(SFIZZ_LIBRARY))
        self._declare()

        self._synth = self._lib.sfizz_create_synth()
        if not self._synth:
            raise RuntimeError("could not create the synth")
        self._lib.sfizz_set_samples_per_block(self._synth, block_size)
        self._lib.sfizz_set_num_voices(self._synth, voices)
        self._lib.sfizz_set_volume(self._synth, 0.8)

        if not self._lib.sfizz_load_file(self._synth, str(self.sfz_path).encode()):
            raise RuntimeError(f"could not load {self.sfz_path}")

        # The audio block, allocated once: rendering fills it and it is copied
        # out to the speakers, so there is nothing to allocate per callback.
        self._buffer = (ctypes.c_float * (2 * block_size))()

    def _declare(self) -> None:
        lib = self._lib
        void_p = ctypes.c_void_p
        lib.sfizz_create_synth.restype = void_p
        lib.sfizz_load_file.argtypes = [void_p, ctypes.c_char_p]
        lib.sfizz_load_file.restype = ctypes.c_bool
        lib.sfizz_set_samples_per_block.argtypes = [void_p, ctypes.c_int]
        lib.sfizz_set_num_voices.argtypes = [void_p, ctypes.c_int]
        lib.sfizz_set_volume.argtypes = [void_p, ctypes.c_float]
        lib.sfizz_send_note_on.argtypes = [void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.sfizz_send_note_off.argtypes = [void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.sfizz_send_cc.argtypes = [void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.sfizz_render_block.argtypes = [
            void_p,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
            ctypes.c_int,
            ctypes.c_int,
        ]

    # ── the keyboard ──────────────────────────────────────────────────────────
    def note_on(self, note: int, velocity: int = 100) -> None:
        with self._lock:
            self._lib.sfizz_send_note_on(self._synth, 0, int(note), int(velocity))

    def note_off(self, note: int) -> None:
        with self._lock:
            self._lib.sfizz_send_note_off(self._synth, 0, int(note), 0)

    def control_change(self, controller: int, value: int) -> None:
        with self._lock:
            self._lib.sfizz_send_cc(self._synth, 0, int(controller), int(value))

    def all_notes_off(self) -> None:
        for controller in (123, 120):  # all notes off, all sound off
            self.control_change(controller, 0)

    # ── the sound ────────────────────────────────────────────────────────────
    def render(self, frames: Optional[int] = None) -> np.ndarray:
        """Render audio, as (frames, 2) float32 in [-1, 1]."""
        frames = int(frames or self.block_size)
        if frames != self.block_size:
            # The block size is fixed when the synth is built, so render in
            # whole blocks and trim.
            out = np.zeros((frames, 2), dtype=np.float32)
            done = 0
            while done < frames:
                chunk = self.render(self.block_size)
                take = min(len(chunk), frames - done)
                out[done : done + take] = chunk[:take]
                done += take
            return out

        left = ctypes.cast(self._buffer, ctypes.POINTER(ctypes.c_float))
        right = ctypes.cast(
            ctypes.byref(self._buffer, self.block_size * ctypes.sizeof(ctypes.c_float)),
            ctypes.POINTER(ctypes.c_float),
        )
        pointers = (ctypes.POINTER(ctypes.c_float) * 2)(left, right)
        with self._lock:
            self._lib.sfizz_render_block(self._synth, pointers, 2, self.block_size)
        flat = np.frombuffer(self._buffer, dtype=np.float32, count=2 * self.block_size)
        return flat.reshape(2, self.block_size).T.copy()


def midi_to_sfz(midi_bytes: bytes) -> Optional[tuple[str, int, int]]:
    """Turn a MIDI message into something the piano understands.

    Returns (kind, number, value) where kind is "on", "off" or "cc".
    """
    if not midi_bytes:
        return None
    status = midi_bytes[0]
    if status >= 0xF0:
        return None
    command = status & 0xF0
    if command == 0x90 and midi_bytes[2] > 0:
        return ("on", midi_bytes[1], midi_bytes[2])
    if command in (0x80, 0x90):
        return ("off", midi_bytes[1], 0)
    if command == 0xB0:
        return ("cc", midi_bytes[1], midi_bytes[2])
    return None
