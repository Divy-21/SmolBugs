"""Reassembles chunked accelerometer packets into complete FFT windows.

The ESP32 sends each 4096-sample window as several smaller MQTT messages:

    {"machine_id": "MG-001", "window_id": 1234, "chunk": 0, "chunks": 8,
     "sample_rate_hz": 4000, "range_g": 2, "first_sample_ms": 96847,
     "units": "g", "x": [...512], "y": [...512], "z": [...512]}

Feed every packet to WindowAssembler.add(); it returns an AssembledWindow once
all chunks of a window have arrived, and drops windows that stall (lost chunk).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

# ADXL355 sensitivity (datasheet typical), 20-bit output.
ADXL355_LSB_PER_G: dict[int, float] = {2: 256_000.0, 4: 128_000.0, 8: 64_000.0}
AXES = ("x", "y", "z")
REQUIRED_KEYS = ("machine_id", "window_id", "chunk", "chunks", "sample_rate_hz", *AXES)


class PacketError(ValueError):
    """A packet is malformed or inconsistent with the rest of its window."""


@dataclass
class AssembledWindow:
    machine_id: str
    window_id: int
    sample_rate_hz: float
    range_g: int
    first_sample_ms: int | None
    accel_g: np.ndarray  # shape (n_samples, 3): columns x, y, z in g

    @property
    def n_samples(self) -> int:
        return int(self.accel_g.shape[0])

    def axis(self, name: str) -> np.ndarray:
        return self.accel_g[:, AXES.index(name)]

    def clipped_fraction(self, threshold_fraction: float = 0.98) -> float:
        """Share of samples near full scale. Clipping corrupts the spectrum."""
        return float(np.mean(np.abs(self.accel_g) >= threshold_fraction * self.range_g))


@dataclass
class _Pending:
    chunks: int
    sample_rate_hz: float
    range_g: int
    first_sample_ms: int | None
    created: float
    parts: dict[int, np.ndarray] = field(default_factory=dict)


@dataclass
class AssemblerStats:
    completed: int = 0
    dropped_incomplete: int = 0
    rejected_packets: int = 0
    duplicate_chunks: int = 0


def _to_g(values: list[Any], units: str, range_g: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if units == "g":
        return arr
    if units == "counts":
        return arr / ADXL355_LSB_PER_G[range_g]
    raise PacketError(f"units must be 'g' or 'counts', got {units!r}")


class WindowAssembler:
    def __init__(self, timeout_s: float = 5.0, clock: Callable[[], float] = time.monotonic):
        self.timeout_s = timeout_s
        self._clock = clock
        self._pending: dict[tuple[str, int], _Pending] = {}
        self.stats = AssemblerStats()

    @property
    def pending_windows(self) -> int:
        return len(self._pending)

    def add(self, packet: dict[str, Any]) -> AssembledWindow | None:
        """Add one packet. Returns the finished window when its last chunk arrives.

        Raises PacketError for malformed packets (and counts them in stats).
        """
        self.expire()
        try:
            return self._add(packet)
        except PacketError:
            self.stats.rejected_packets += 1
            raise

    def _add(self, packet: dict[str, Any]) -> AssembledWindow | None:
        missing = [k for k in REQUIRED_KEYS if k not in packet]
        if missing:
            raise PacketError(f"packet missing keys: {missing}")

        try:
            machine_id = str(packet["machine_id"])
            window_id = int(packet["window_id"])
            chunk = int(packet["chunk"])
            chunks = int(packet["chunks"])
            fs = float(packet["sample_rate_hz"])
            range_g = int(packet.get("range_g", 2))
        except (TypeError, ValueError) as exc:
            raise PacketError(f"bad header field: {exc}") from exc

        if chunks < 1 or not 0 <= chunk < chunks:
            raise PacketError(f"chunk {chunk} out of range for {chunks} chunks")
        if fs <= 0:
            raise PacketError("sample_rate_hz must be positive")
        if range_g not in ADXL355_LSB_PER_G:
            raise PacketError(f"range_g must be one of {sorted(ADXL355_LSB_PER_G)}")

        lengths = {len(packet[a]) for a in AXES}
        if len(lengths) != 1 or 0 in lengths:
            raise PacketError("x, y and z must be non-empty and the same length")
        units = packet.get("units", "g")
        data = np.column_stack([_to_g(packet[a], units, range_g) for a in AXES])
        if not np.all(np.isfinite(data)):
            raise PacketError("samples contain NaN or inf")

        key = (machine_id, window_id)
        pending = self._pending.get(key)
        if pending is None:
            first_ms = packet.get("first_sample_ms") if chunk == 0 else None
            pending = _Pending(chunks, fs, range_g, first_ms, self._clock())
            self._pending[key] = pending
        elif pending.chunks != chunks or pending.sample_rate_hz != fs or pending.range_g != range_g:
            raise PacketError(f"window {key} has inconsistent chunks/sample_rate/range between packets")

        if chunk in pending.parts:
            self.stats.duplicate_chunks += 1
            return None
        if chunk == 0 and pending.first_sample_ms is None:
            pending.first_sample_ms = packet.get("first_sample_ms")
        pending.parts[chunk] = data

        if len(pending.parts) < pending.chunks:
            return None

        del self._pending[key]
        self.stats.completed += 1
        return AssembledWindow(
            machine_id=machine_id,
            window_id=window_id,
            sample_rate_hz=fs,
            range_g=range_g,
            first_sample_ms=pending.first_sample_ms,
            accel_g=np.vstack([pending.parts[i] for i in range(pending.chunks)]),
        )

    def expire(self) -> list[tuple[str, int]]:
        """Drop windows that have waited longer than timeout_s (a chunk was lost)."""
        now = self._clock()
        stale = [k for k, p in self._pending.items() if now - p.created > self.timeout_s]
        for k in stale:
            del self._pending[k]
        self.stats.dropped_incomplete += len(stale)
        return stale
