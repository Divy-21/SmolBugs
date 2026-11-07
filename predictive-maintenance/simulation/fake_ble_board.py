"""A fake MachineGuard board that behaves like the Bluetooth client the receiver expects.

Sends thermistor text every 0.5 s and a vibration window (START + DATA frames)
every few seconds, using the same binary format as the firmware.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from processing.ble_protocol import TEMPERATURE_CHAR_UUID, VIBRATION_CHAR_UUID, window_to_frames
from simulation.packet_simulator import CONDITIONS, simulate_window_signals


@dataclass
class FakeDevice:
    name: str = "MachineGuard"
    address: str = "SIM:00:00:00:00:01"


class FakeBoardScanner:
    @staticmethod
    async def find_device_by_name(name: str, timeout: float = 15.0):
        return FakeDevice(name=name)


class FakeBoardClient:
    """Async context manager mimicking bleak.BleakClient closely enough for the receiver."""

    mtu_size = 247
    window_interval_s = 2.0
    frame_gap_s = 0.002
    conditions = list(CONDITIONS)

    def __init__(self, device, **_kwargs):
        self.device = device
        self.is_connected = False
        self._callbacks = {}
        self._tasks: list[asyncio.Task] = []

    async def __aenter__(self):
        self.is_connected = True
        return self

    async def __aexit__(self, *exc):
        self.is_connected = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def start_notify(self, uuid: str, callback):
        self._callbacks[uuid] = callback
        if uuid == TEMPERATURE_CHAR_UUID:
            self._tasks.append(asyncio.create_task(self._temperature_loop(callback)))
        elif uuid == VIBRATION_CHAR_UUID:
            self._tasks.append(asyncio.create_task(self._vibration_loop(callback)))
        else:
            raise ValueError(f"unknown characteristic {uuid}")

    async def _temperature_loop(self, callback):
        seq = 0
        while self.is_connected:
            seq += 1
            mv = 1582 + (seq % 5) - 2
            ohm = int(75_000 * mv / (3300 - mv))
            callback(None, bytearray(f"{seq},{mv},{ohm}".encode()))
            await asyncio.sleep(0.5)

    async def _vibration_loop(self, callback):
        window_id = 0
        while self.is_connected:
            condition = self.conditions[window_id % len(self.conditions)]
            accel = simulate_window_signals(condition, seed=window_id)
            for frame in window_to_frames(accel, window_id=window_id, first_sample_ms=window_id * 10_000):
                callback(None, bytearray(frame))
                await asyncio.sleep(self.frame_gap_s)
            window_id = (window_id + 1) % 65536
            await asyncio.sleep(self.window_interval_s)
