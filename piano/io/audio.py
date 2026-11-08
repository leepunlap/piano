"""Audio rendering through the sfizz SFZ engine.

`sfizz` is used as an external process rather than a linked library so the
pipeline stays portable and the rendering step can be swapped for a different
engine without touching the rest of the code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

import numpy as np
import soundfile as sf

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SFIZZ_RENDER = os.path.join(REPO_ROOT, "bin", "sfizz_render")

DEFAULT_SFZ = os.path.join(
    REPO_ROOT, "samples", "SalamanderGrandPiano", "Salamander Grand Piano V3.sfz"
)
DEFAULT_SPLENDID_SFZ = os.path.join(
    REPO_ROOT, "samples", "SplendidGrandPiano", "Splendid Grand Piano.sfz"
)

SAMPLE_RATE = 48000


@dataclass
class RenderResult:
    """Outcome of a render: the audio plus where it came from."""

    audio: np.ndarray
    sample_rate: int
    sfz_path: str
    wav_path: str | None = None

    @property
    def duration(self) -> float:
        if self.sample_rate == 0:
            return 0.0
        return len(self.audio) / self.sample_rate

    def peak(self) -> float:
        if self.audio.size == 0:
            return 0.0
        return float(np.max(np.abs(self.audio)))


def resolve_sfz(path: str | None = None) -> str:
    """Locate an SFZ file, defaulting to Salamander."""
    candidate = path or DEFAULT_SFZ
    if not os.path.isfile(candidate):
        raise FileNotFoundError(f"SFZ file not found: {candidate}")
    return candidate


def render_midi_to_wav(
    midi_path: str,
    wav_path: str,
    sfz_path: str | None = None,
    sample_rate: int = SAMPLE_RATE,
    polyphony: int = 64,
) -> str:
    """Render a MIDI file to WAV using sfizz."""
    if not os.path.isfile(SFIZZ_RENDER):
        raise FileNotFoundError(
            f"sfizz_render not found at {SFIZZ_RENDER}. "
            "Download from https://github.com/sfztools/sfizz/releases"
        )

    sfz = resolve_sfz(sfz_path)
    os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)

    cmd = [
        SFIZZ_RENDER,
        "--sfz", sfz,
        "--midi", midi_path,
        "--wav", wav_path,
        "--samplerate", str(sample_rate),
        "--polyphony", str(polyphony),
        "--use-eot",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.isfile(wav_path):
        raise RuntimeError(
            f"sfizz render failed ({result.returncode}):\n{result.stderr[:2000]}"
        )
    return wav_path


def load_wav(path: str) -> tuple[np.ndarray, int]:
    """Load a WAV file as float32 in [-1, 1], returning (audio, sample_rate)."""
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim == 1:
        audio = np.stack([audio, audio], axis=1)
    return audio, sample_rate


def render(
    midi_path: str,
    wav_path: str,
    sfz_path: str | None = None,
    sample_rate: int = SAMPLE_RATE,
) -> RenderResult:
    """Render and load in one step."""
    render_midi_to_wav(midi_path, wav_path, sfz_path, sample_rate)
    audio, rate = load_wav(wav_path)
    return RenderResult(audio=audio, sample_rate=rate, sfz_path=resolve_sfz(sfz_path), wav_path=wav_path)


def rms(audio: np.ndarray) -> float:
    """Root-mean-square level of an audio buffer."""
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def peak_normalize(audio: np.ndarray, target: float = 0.89) -> np.ndarray:
    """Scale audio so its peak hits `target`, leaving silence untouched."""
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-9:
        return audio
    return (audio * (target / peak)).astype(np.float32)


def available_engines() -> list[str]:
    """Which SFZ engines are available on this machine."""
    engines: list[str] = []
    if os.path.isfile(SFIZZ_RENDER):
        engines.append("sfizz")
    for name in ("sforzando", "qsampler", "linuxsampler"):
        if shutil.which(name):
            engines.append(name)
    return engines
