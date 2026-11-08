"""Playing audio out through the Mac's speakers.

The reference song the player follows should sound like the instrument the
system will eventually render with.  Sending MIDI to the keyboard instead
would tie the guide track to whatever sound module the hardware happens to
have (and the Nord's own sound module may be off, or on a different patch),
so the reference is rendered with sfizz and played locally instead.

This module provides a small streaming player that can start, stop and
scrub, plus a one-shot helper for playing a WAV file.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - optional dependency
    sd = None  # type: ignore

DEFAULT_SAMPLE_RATE = 48000


class AudioOutputError(RuntimeError):
    """Raised when audio output is unavailable."""


def available() -> bool:
    """Whether local audio playback is possible."""
    return sd is not None


def output_devices() -> list[str]:
    """Names of the available output devices."""
    if sd is None:
        return []
    try:
        return [
            device["name"]
            for device in sd.query_devices()
            if device["max_output_channels"] > 0
        ]
    except Exception:
        return []


def default_device() -> Optional[str]:
    """Name of the default output device."""
    if sd is None:
        return None
    try:
        return sd.query_devices(kind="output")["name"]
    except Exception:
        return None


class AudioPlayer:
    """Streams an audio buffer to the speakers.

    The buffer is played from a background thread, so the caller can keep
    running (recording MIDI, updating progress) while the audio plays.

    `start_offset` lets the reference begin a little late to give the player
    time to get ready after the count-in.

    `gain_db` is applied before playback.  Rendered sample libraries often
    come out far below a comfortable listening level (a piano render can sit
    around -35 dBFS RMS), so a sensible default gain is applied rather than
    leaving the player to strain to hear the guide.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        device: Optional[str] = None,
        gain_db: float = 12.0,
        normalise: bool = True,
        target_rms: float = 0.12,
    ) -> None:
        if sd is None:
            raise AudioOutputError("sounddevice is not installed (pip install sounddevice)")
        self.sample_rate = sample_rate
        self.device = device
        self.gain_db = gain_db
        self.normalise = normalise
        self.target_rms = target_rms
        self._stream: Optional[object] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._started_at = 0.0
        self._paused = False
        self._paused_at = 0.0

    def _prepare(self, audio: np.ndarray) -> np.ndarray:
        """Apply gain, and optionally normalise to a comfortable level."""
        if audio.ndim == 1:
            audio = np.stack([audio, audio], axis=1)
        audio = np.ascontiguousarray(audio, dtype=np.float32)

        if self.normalise and audio.size:
            rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
            if rms > 1e-6:
                wanted = self.target_rms
                gain = wanted / rms
                # Never push the peak into clipping.
                peak = float(np.max(np.abs(audio)))
                if peak * gain > 0.95:
                    gain = 0.95 / peak
                audio = audio * gain

        if self.gain_db:
            audio = audio * (10.0 ** (self.gain_db / 20.0))

        # Hard safety clip.
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0.99:
            audio = audio * (0.99 / peak)
        return audio

    # -- playback ---------------------------------------------------------

    def play(self, audio: np.ndarray, start_offset: float = 0.0) -> None:
        """Start playing `audio` in the background."""
        self.stop()

        audio = self._prepare(audio)

        # Pad with silence so the playback starts `start_offset` seconds late.
        if start_offset > 0:
            pad = np.zeros((int(start_offset * self.sample_rate), audio.shape[1]), dtype=np.float32)
            audio = np.concatenate([pad, audio], axis=0)

        self._stop.clear()
        self._started_at = time.monotonic()

        def run() -> None:
            try:
                with sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=audio.shape[1],
                    dtype="float32",
                    device=self.device,
                    latency="low",
                ) as stream:
                    self._stream = stream
                    block = 1024
                    for start in range(0, len(audio), block):
                        if self._stop.is_set():
                            break
                        chunk = audio[start : start + block]
                        stream.write(chunk)
                        if self._paused:
                            # Busy-wait while paused, keeping the stream alive.
                            while self._paused and not self._stop.is_set():
                                time.sleep(0.01)
            except Exception:
                pass
            finally:
                self._stream = None

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop playback immediately."""
        self._stop.set()
        self._paused = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until playback finishes."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    @property
    def elapsed(self) -> float:
        if self._thread is None:
            return 0.0
        return time.monotonic() - self._started_at

    def __enter__(self) -> "AudioPlayer":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def play_wav(path: str, device: Optional[str] = None) -> AudioPlayer:
    """Play a WAV file in the background."""
    import soundfile as sf

    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    player = AudioPlayer(sample_rate=rate, device=device)
    player.play(audio)
    return player


def render_reference(
    reference,
    sfz_path: Optional[str] = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    work_dir: str = "work",
):
    """Render a reference sequence to audio for use as a guide track.

    Returns (audio, sample_rate).
    """
    from ..io.audio import render
    from ..io.midi_io import write_midi_with_tempo_map

    os.makedirs(work_dir, exist_ok=True)
    midi_path = os.path.join(work_dir, "_reference_guide.mid")
    wav_path = os.path.join(work_dir, "_reference_guide.wav")

    write_midi_with_tempo_map(reference, midi_path)
    result = render(midi_path, wav_path, sfz_path, sample_rate)
    return result.audio, result.sample_rate
