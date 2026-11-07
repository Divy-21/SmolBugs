"""Bluetooth receiver for the MachineGuard (machine1) board.

Subscribes to both characteristics:
  - temperature: text 'sequence,voltage_mV,resistance_ohms' every 500 ms
  - vibration:   binary START/DATA frames (see processing/ble_protocol.py)

Run from the predictive-maintenance folder:
    python -m ingest.ble_receiver                    # real board
    python -m ingest.ble_receiver --simulate         # no hardware needed
    python -m ingest.ble_receiver --rpm 3000 --out data/session1
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from processing import FFTConfig, ThermistorConfig, analyze_window, parse_thermistor_message
from processing.assembler import PacketError
from processing.ble_protocol import MIN_MTU, TEMPERATURE_CHAR_UUID, VIBRATION_CHAR_UUID, BleVibrationDecoder

DEVICE_NAME = "MachineGuard"


class MachineGuardHandler:
    """Everything that happens to incoming data, independent of Bluetooth itself."""

    def __init__(
        self,
        machine_id: str = "MG-001",
        rpm: float | None = None,
        out_dir: Path | None = None,
        thermistor: ThermistorConfig = ThermistorConfig(),
        fft: FFTConfig = FFTConfig(),
        log: Callable[[str], None] = print,
    ):
        self.machine_id = machine_id
        self.rpm = rpm
        self.thermistor = thermistor
        self.fft = fft
        self.log = log
        self.decoder = BleVibrationDecoder(machine_id)
        self.bad_frames = 0
        self.temperature_count = 0
        self.window_count = 0
        self.out_dir = Path(out_dir) if out_dir else None
        if self.out_dir:
            (self.out_dir / "windows").mkdir(parents=True, exist_ok=True)

    # -- temperature ------------------------------------------------------
    def on_temperature(self, data: bytes) -> dict[str, Any] | None:
        try:
            reading = parse_thermistor_message(bytes(data).decode("utf-8"), self.thermistor)
        except (ValueError, UnicodeDecodeError):
            self.log(f"Invalid temperature message: {bytes(data)!r}")
            return None
        self.temperature_count += 1
        reading["received_at_utc"] = _now()
        temp = reading["temperature_c"]
        temp_text = f"{temp:6.2f} °C" if temp is not None else "  wiring?"
        self.log(f"Temp  #{reading['sequence']:>5} | {reading['voltage_mv']:>4} mV | "
                 f"{reading['resistance_ohm'] / 1000:>7.2f} kΩ | {temp_text}")
        self._append_jsonl("temperature.jsonl", reading)
        return reading

    # -- vibration --------------------------------------------------------
    def on_vibration(self, data: bytes) -> dict[str, Any] | None:
        try:
            window = self.decoder.feed(data)
        except PacketError as err:
            self.bad_frames += 1
            self.log(f"Bad vibration frame: {err}")
            return None
        if window is None:
            return None

        self.window_count += 1
        result = analyze_window(window, self.fft, rpm=self.rpm)
        result["received_at_utc"] = _now()
        x = result["axes"]["x"]
        line = (f"Vib   window {window.window_id:>5} | dominant {x['dominant_freq_hz']:7.2f} Hz "
                f"@ {x['dominant_amp_g']:.3f} g | velocity {x['velocity_rms_mm_s']:.2f} mm/s")
        if "harmonics" in x:
            line += " | " + " ".join(f"{h['order']}x={h['amp_g']:.3f}" for h in x["harmonics"])
        self.log(line)
        for warning in result["warnings"]:
            self.log(f"      warning: {warning}")

        self._append_jsonl("vibration.jsonl", result)
        if self.out_dir:
            np.savez_compressed(
                self.out_dir / "windows" / f"{self.machine_id}_w{window.window_id:05d}.npz",
                accel_g=window.accel_g.astype(np.float32),
                sample_rate_hz=window.sample_rate_hz,
                first_sample_ms=window.first_sample_ms or 0,
            )
        return result

    def summary(self) -> str:
        s = self.decoder.stats
        a = self.decoder.assembler.stats
        return (f"temperature readings: {self.temperature_count} | windows: {self.window_count} | "
                f"vibration frames: {s.frames} | bad frames: {self.bad_frames} | "
                f"lost START frames: {s.orphan_data_frames} | incomplete windows dropped: {a.dropped_incomplete}")

    def _append_jsonl(self, name: str, record: dict[str, Any]) -> None:
        if self.out_dir:
            with open(self.out_dir / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run(handler: MachineGuardHandler, scanner_cls=None, client_cls=None,
              duration_s: float | None = None) -> bool:
    """Connect to the board and stream until disconnected (or duration_s elapses)."""
    if scanner_cls is None or client_cls is None:
        from bleak import BleakClient, BleakScanner
        scanner_cls = scanner_cls or BleakScanner
        client_cls = client_cls or BleakClient

    handler.log(f"Scanning for {DEVICE_NAME}...")
    device = await scanner_cls.find_device_by_name(DEVICE_NAME, timeout=15.0)
    if device is None:
        handler.log(f"{DEVICE_NAME} not found. Check the board is powered on and advertising.")
        return False

    handler.log(f"Found {device.name} [{device.address}], connecting...")
    async with client_cls(device) as client:
        mtu = getattr(client, "mtu_size", None)
        handler.log(f"Connected. MTU: {mtu}")
        if mtu is not None and mtu < MIN_MTU:
            handler.log(f"WARNING: MTU {mtu} < {MIN_MTU}; vibration frames will not fit and the "
                        "board will skip sending them.")

        await client.start_notify(TEMPERATURE_CHAR_UUID, lambda _s, d: handler.on_temperature(d))
        try:
            await client.start_notify(VIBRATION_CHAR_UUID, lambda _s, d: handler.on_vibration(d))
        except Exception as err:  # older firmware without the vibration characteristic
            handler.log(f"Vibration characteristic not available ({err}); temperature only.")

        handler.log("Receiving. Press Ctrl+C to stop.\n")
        elapsed = 0.0
        while client.is_connected and (duration_s is None or elapsed < duration_s):
            await asyncio.sleep(0.2)
            elapsed += 0.2
    handler.log("\n" + handler.summary())
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SmolBugs Bluetooth receiver")
    parser.add_argument("--machine-id", default="MG-001")
    parser.add_argument("--rpm", type=float, help="machine speed, enables 1x/2x/3x harmonics")
    parser.add_argument("--out", type=Path, help="folder to save temperature, results and raw windows")
    parser.add_argument("--simulate", action="store_true", help="fake board, no Bluetooth needed")
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    args = parser.parse_args(argv)

    handler = MachineGuardHandler(args.machine_id, rpm=args.rpm, out_dir=args.out)
    scanner_cls = client_cls = None
    if args.simulate:
        from simulation.fake_ble_board import FakeBoardClient, FakeBoardScanner
        scanner_cls, client_cls = FakeBoardScanner, FakeBoardClient
        handler.log("SIMULATION MODE: fake MachineGuard board\n")
    try:
        asyncio.run(run(handler, scanner_cls, client_cls, args.duration))
    except KeyboardInterrupt:
        handler.log("\nStopped.\n" + handler.summary())


if __name__ == "__main__":
    main()
