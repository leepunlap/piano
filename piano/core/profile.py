"""Learning the player's personal timing and dynamics.

The system should not "fix" a player into sounding like a machine.  What it
should do is learn *their* tendencies and remove only the noise.

The profile records, per position in the score:

* the distribution of onset error (how early/late they play that note),
* how they map raw MIDI velocity to musical dynamic.

Both are estimated from aligned takes: the alignment tells us which played
note corresponds to which score note, and the difference tells us the error.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Sequence

from .align import Alignment
from .notes import Note, Sequence


@dataclass
class TimingStats:
    """Running statistics of onset error for one position.

    Uses Welford's algorithm for a numerically stable mean/variance.
    """

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    min_error: float = float("inf")
    max_error: float = float("-inf")
    errors: list[float] = field(default_factory=list)

    def add(self, error: float, keep_history: bool = True) -> None:
        self.count += 1
        delta = error - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (error - self.mean)
        self.min_error = min(self.min_error, error)
        self.max_error = max(self.max_error, error)
        if keep_history:
            self.errors.append(error)

    @property
    def variance(self) -> float:
        if self.count < 2:
            return 0.0
        return self.m2 / (self.count - 1)

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "mean": round(self.mean, 6),
            "std": round(self.std, 6),
            "min": None if self.count == 0 else round(self.min_error, 6),
            "max": None if self.count == 0 else round(self.max_error, 6),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimingStats":
        stats = cls()
        stats.count = int(data.get("count", 0))
        stats.mean = float(data.get("mean", 0.0))
        std = float(data.get("std", 0.0))
        stats.m2 = std * std * max(0, stats.count - 1)
        if data.get("min") is not None:
            stats.min_error = float(data["min"])
        if data.get("max") is not None:
            stats.max_error = float(data["max"])
        return stats


@dataclass
class VelocityStats:
    """Relationship between raw MIDI velocity and musical dynamic.

    Fits `intended ~ slope * raw + intercept` by least squares.  When the
    intended velocities carry no variation there is nothing to learn, so the
    curve degrades to the identity rather than collapsing to zero slope.
    """

    count: int = 0
    slope: float = 1.0
    intercept: float = 0.0
    residual_std: float = 0.0
    _sum_x: float = 0.0
    _sum_y: float = 0.0
    _sum_xy: float = 0.0
    _sum_xx: float = 0.0
    _sum_yy: float = 0.0

    def add(self, raw: float, intended: float) -> None:
        self.count += 1
        self._sum_x += raw
        self._sum_y += intended
        self._sum_xy += raw * intended
        self._sum_xx += raw * raw
        self._sum_yy += intended * intended

    def fit(self) -> None:
        """Least-squares fit of intended ~ slope * raw + intercept."""
        if self.count < 2:
            self.slope = 1.0
            self.intercept = 0.0
            return

        n = self.count
        mean_x = self._sum_x / n
        mean_y = self._sum_y / n

        # If the intended velocities are effectively constant there is no
        # mapping to learn - keep the raw values as they are.
        intended_spread = self._sum_yy / n - mean_y * mean_y
        if intended_spread < 1e-6:
            self.slope = 1.0
            self.intercept = 0.0
            self.residual_std = 0.0
            return

        variance_x = self._sum_xx / n - mean_x * mean_x
        if variance_x < 1e-9:
            self.slope = 1.0
            self.intercept = mean_y - mean_x
            self.residual_std = 0.0
            return

        covariance = self._sum_xy / n - mean_x * mean_y
        self.slope = covariance / variance_x
        self.intercept = mean_y - self.slope * mean_x

        # Residual spread around the fitted line.
        residual_variance = intended_spread - self.slope * covariance
        self.residual_std = math.sqrt(max(0.0, residual_variance))

    def apply(self, raw: float) -> int:
        """Map a raw velocity to the player's intended velocity."""
        value = self.slope * raw + self.intercept
        return int(max(1, min(127, round(value))))

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "slope": round(self.slope, 6),
            "intercept": round(self.intercept, 6),
            "residual_std": round(self.residual_std, 6),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VelocityStats":
        stats = cls()
        stats.count = int(data.get("count", 0))
        stats.slope = float(data.get("slope", 1.0))
        stats.intercept = float(data.get("intercept", 0.0))
        stats.residual_std = float(data.get("residual_std", 0.0))
        return stats


@dataclass
class PlayerProfile:
    """Everything the system has learned about one player.

    `position_stats` is keyed by the index of the note in the score's strike
    list, so different songs keep separate timing profiles unless explicitly
    merged.
    """

    player_id: str = "default"
    song_id: str = ""
    global_timing: TimingStats = field(default_factory=TimingStats)
    position_timing: dict[int, TimingStats] = field(default_factory=dict)
    velocity: VelocityStats = field(default_factory=VelocityStats)
    takes: int = 0

    # -- learning ---------------------------------------------------------

    def observe(
        self,
        alignment: Alignment,
        reference: Sequence,
        performance: Sequence,
        intended_velocities: Sequence[int] | None = None,
    ) -> int:
        """Absorb one aligned take into the profile.

        Returns the number of matched notes that contributed.
        """
        ref_strikes = reference.strikes()
        perf_strikes = performance.strikes()
        contributed = 0

        for ref_index, perf_index in alignment.pairs:
            if ref_index >= len(ref_strikes) or perf_index >= len(perf_strikes):
                continue
            ref_note = ref_strikes[ref_index]
            perf_note = perf_strikes[perf_index]

            error = perf_note.start - ref_note.start
            self.global_timing.add(error)
            self.position_timing.setdefault(ref_index, TimingStats()).add(error)

            intended = ref_note.velocity
            if intended_velocities is not None and ref_index < len(intended_velocities):
                intended = intended_velocities[ref_index]
            self.velocity.add(float(perf_note.velocity), float(intended))
            contributed += 1

        if contributed:
            self.velocity.fit()
            self.takes += 1
        return contributed

    # -- inference --------------------------------------------------------

    def expected_offset(self, position: int | None = None, default: float = 0.0) -> float:
        """The player's typical offset for a note.

        Falls back to the global mean when a position has little evidence, and
        to `default` when nothing is known yet.
        """
        if position is not None:
            stats = self.position_timing.get(position)
            if stats and stats.count >= 2:
                return stats.mean
        if self.global_timing.count >= 2:
            return self.global_timing.mean
        return default

    def is_confident(self, position: int | None = None) -> bool:
        """Whether we trust the offset enough to act on it."""
        if position is not None:
            stats = self.position_timing.get(position)
            if stats and stats.count >= 3:
                return stats.std < 0.12
        return self.global_timing.count >= 3 and self.global_timing.std < 0.15

    def summary(self) -> str:
        if self.takes == 0:
            return "No takes learned yet."
        mean_ms = self.global_timing.mean * 1000.0
        std_ms = self.global_timing.std * 1000.0
        sign = "late" if mean_ms > 0 else "early" if mean_ms < 0 else "on time"
        lines = [
            f"takes: {self.takes}",
            f"timing: {mean_ms:+.1f} ms ({sign}), sd {std_ms:.1f} ms",
            f"velocity: out={self.velocity.slope:.3f}*raw{self.velocity.intercept:+.1f}, "
            f"residual sd {self.velocity.residual_std:.2f}",
        ]
        return "\n".join(lines)

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "song_id": self.song_id,
            "takes": self.takes,
            "global_timing": self.global_timing.to_dict(),
            "position_timing": {str(k): v.to_dict() for k, v in self.position_timing.items()},
            "velocity": self.velocity.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlayerProfile":
        profile = cls(
            player_id=data.get("player_id", "default"),
            song_id=data.get("song_id", ""),
            takes=int(data.get("takes", 0)),
            global_timing=TimingStats.from_dict(data.get("global_timing", {})),
            velocity=VelocityStats.from_dict(data.get("velocity", {})),
        )
        for key, value in (data.get("position_timing") or {}).items():
            profile.position_timing[int(key)] = TimingStats.from_dict(value)
        return profile

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1)
        return path

    @classmethod
    def load(cls, path: str) -> "PlayerProfile":
        with open(path, encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


def profile_path(base_dir: str, player_id: str, song_id: str = "") -> str:
    """Where a profile is stored on disk."""
    name = f"{player_id}__{song_id}.json" if song_id else f"{player_id}__global.json"
    return os.path.join(base_dir, name)
