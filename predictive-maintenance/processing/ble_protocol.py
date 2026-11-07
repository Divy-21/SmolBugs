"""Binary Bluetooth (BLE) protocol for vibration windows.

The ESP32 captures one window (4096 samples) then sends it as notifications on the
vibration characteristic. All values are little-endian.

START frame (17 bytes), sent once before each window:
    u8  type = 0x01
    u16 window_id          increments per window, wraps at 65535
    f32 sample_rate_hz     the sensor's actual output rate
    u16 n_samples          samples in the window (4096)
    u8  samples_per_chunk  readings per DATA frame (32)
    u8  range_g            accelerometer range: 2, 4 or 8
    u16 lsb_per_g          int16 counts per g (16000 at +/-2 g)
    u32 first_sample_ms    ESP32 millis() when the window started

DATA frame (5 + 6*k bytes, k <= samples_per_chunk; 197 bytes when full):
    u8  type = 0x02
    u16 window_id
    u16 chunk              0 .. chunks-1
    k x (i16 x, i16 y, i16 z)

The matching C++ encoder is smolbugs_protocol.h in the machine1 sketch folder.
A full DATA frame needs a BLE MTU of at least 200 (notify payload = MTU - 3).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from .assembler import AssembledWindow, PacketError, WindowAssembler

FRAME_START = 0x01
FRAME_DATA = 0x02
START_STRUCT = struct.Struct("<BHfHBBHI")
DATA_HEADER = struct.Struct("<BHH")
BYTES_PER_SAMPLE = 6
SAMPLES_PER_CHUNK = 32
MIN_MTU = DATA_HEADER.size + SAMPLES_PER_CHUNK * BYTES_PER_SAMPLE + 3  # 200

# int16 counts per g. ADXL355 20-bit data shifted right by 4 bits.
LSB_PER_G_INT16: dict[int, int] = {2: 16000, 4: 8000, 8: 4000}

# BLE UUIDs (Nordic UART base). TX carries the existing thermistor text messages.
SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
TEMPERATURE_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
VIBRATION_CHAR_UUID = "6E400004-B5A3-F393-E0A9-E50E24DCCA9E"


@dataclass(frozen=True)
class StartFrame:
    window_id: int
    sample_rate_hz: float
    n_samples: int
    samples_per_chunk: int
    range_g: int
    lsb_per_g: int
    first_sample_ms: int

    @property
    def chunks(self) -> int:
        return -(-self.n_samples // self.samples_per_chunk)

    def samples_in_chunk(self, chunk: int) -> int:
        return min(self.samples_per_chunk, self.n_samples - chunk * self.samples_per_chunk)


@dataclass(frozen=True)
class DataFrame:
    window_id: int
    chunk: int
    counts: np.ndarray  # (k, 3) int16


def parse_frame(data: bytes) -> StartFrame | DataFrame:
    data = bytes(data)
    if not data:
        raise PacketError("empty frame")
    kind = data[0]
    if kind == FRAME_START:
        if len(data) != START_STRUCT.size:
            raise PacketError(f"START frame must be {START_STRUCT.size} bytes, got {len(data)}")
        _, wid, fs, n, spc, rng, lsb, first_ms = START_STRUCT.unpack(data)
        if not (np.isfinite(fs) and fs > 0):
            raise PacketError(f"bad sample rate {fs}")
        if n == 0 or spc == 0:
            raise PacketError("n_samples and samples_per_chunk must be > 0")
        if rng not in LSB_PER_G_INT16 or lsb == 0:
            raise PacketError(f"bad range_g {rng} / lsb_per_g {lsb}")
        return StartFrame(wid, float(fs), n, spc, rng, lsb, first_ms)
    if kind == FRAME_DATA:
        body = len(data) - DATA_HEADER.size
        if body <= 0 or body % BYTES_PER_SAMPLE:
            raise PacketError(f"DATA frame has {len(data)} bytes; not header + whole samples "
                              f"(frame truncated? check the BLE MTU is at least {MIN_MTU})")
        _, wid, chunk = DATA_HEADER.unpack_from(data)
        counts = np.frombuffer(data, dtype="<i2", offset=DATA_HEADER.size).reshape(-1, 3)
        return DataFrame(wid, chunk, counts)
    raise PacketError(f"unknown frame type 0x{kind:02x}")


def encode_start(start: StartFrame) -> bytes:
    return START_STRUCT.pack(FRAME_START, start.window_id, start.sample_rate_hz, start.n_samples,
                             start.samples_per_chunk, start.range_g, start.lsb_per_g, start.first_sample_ms)


def encode_data(window_id: int, chunk: int, counts: np.ndarray) -> bytes:
    counts = np.asarray(counts)
    if counts.ndim != 2 or counts.shape[1] != 3:
        raise ValueError("counts must have shape (k, 3)")
    return DATA_HEADER.pack(FRAME_DATA, window_id, chunk) + counts.astype("<i2").tobytes()


def g_to_counts(accel_g: np.ndarray, lsb_per_g: int) -> np.ndarray:
    """Quantise to int16 counts, saturating at the int16 limits (like the firmware)."""
    return np.clip(np.rint(np.asarray(accel_g) * lsb_per_g), -32768, 32767).astype(np.int16)


def window_to_frames(
    accel_g: np.ndarray,
    window_id: int = 0,
    sample_rate_hz: float = 4000.0,
    range_g: int = 2,
    first_sample_ms: int = 0,
    samples_per_chunk: int = SAMPLES_PER_CHUNK,
) -> list[bytes]:
    """Encode one window exactly as the ESP32 sends it (for tests and simulation)."""
    counts = g_to_counts(accel_g, LSB_PER_G_INT16[range_g])
    start = StartFrame(window_id, sample_rate_hz, counts.shape[0], samples_per_chunk, range_g,
                       LSB_PER_G_INT16[range_g], first_sample_ms)
    frames = [encode_start(start)]
    for c in range(start.chunks):
        frames.append(encode_data(window_id, c, counts[c * samples_per_chunk:(c + 1) * samples_per_chunk]))
    return frames


@dataclass
class DecoderStats:
    frames: int = 0
    start_frames: int = 0
    data_frames: int = 0
    orphan_data_frames: int = 0  # DATA arrived without its START (START was lost)


@dataclass
class BleVibrationDecoder:
    """Turns raw vibration notifications into complete AssembledWindows."""

    machine_id: str
    assembler: WindowAssembler = field(default_factory=lambda: WindowAssembler(timeout_s=10.0))
    stats: DecoderStats = field(default_factory=DecoderStats)
    max_open_windows: int = 8

    def __post_init__(self) -> None:
        self._starts: dict[int, StartFrame] = {}

    def feed(self, data: bytes) -> AssembledWindow | None:
        """Process one notification. Raises PacketError for malformed frames."""
        self.stats.frames += 1
        frame = parse_frame(data)

        if isinstance(frame, StartFrame):
            self.stats.start_frames += 1
            self._starts[frame.window_id] = frame
            while len(self._starts) > self.max_open_windows:
                self._starts.pop(next(iter(self._starts)))
            return None

        self.stats.data_frames += 1
        start = self._starts.get(frame.window_id)
        if start is None:
            self.stats.orphan_data_frames += 1
            return None
        if frame.chunk >= start.chunks:
            raise PacketError(f"chunk {frame.chunk} out of range for {start.chunks} chunks")
        expected = start.samples_in_chunk(frame.chunk)
        if frame.counts.shape[0] != expected:
            raise PacketError(
                f"chunk {frame.chunk} has {frame.counts.shape[0]} samples, expected {expected} "
                "(frame truncated? check the BLE MTU is at least 200)"
            )

        g = frame.counts.astype(float) / start.lsb_per_g
        window = self.assembler.add({
            "machine_id": self.machine_id,
            "window_id": start.window_id,
            "chunk": frame.chunk,
            "chunks": start.chunks,
            "sample_rate_hz": start.sample_rate_hz,
            "range_g": start.range_g,
            "first_sample_ms": start.first_sample_ms,
            "units": "g",
            "x": g[:, 0], "y": g[:, 1], "z": g[:, 2],
        })
        if window is not None:
            self._starts.pop(frame.window_id, None)
        return window
