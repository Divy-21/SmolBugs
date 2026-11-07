"""Bluetooth receiver for the MachineGuard (machine1) board.

Subscribes to both characteristics:
  - temperature: text 'sequence,voltage_mV,resistance_ohms' every 500 ms, or newline-terminated
                 JSON status messages ({"machine_id", "timestamp_ms", "sensors": [...]}) that
                 arrive split across several notifications
  - vibration:   binary START/DATA frames (see processing/ble_protocol.py)

Run from the predictive-maintenance folder:
    python -m ingest.ble_receiver                    # real board
    python -m ingest.ble_receiver --simulate         # no hardware needed
    python -m ingest.ble_receiver --rpm 3000 --out data/session1
    python -m ingest.ble_receiver --rpm 3000 --out data/session1 --live-html
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
from processing.temperature import thermistor_resistance, voltage_to_celsius
from processing.assembler import PacketError
from processing.ble_protocol import MIN_MTU, TEMPERATURE_CHAR_UUID, VIBRATION_CHAR_UUID, BleVibrationDecoder

DEVICE_NAME = "MachineGuard"
MAX_JSON_BUFFER = 8192  # bytes; guards against a message that never ends


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
        live_html: bool = False,
    ):
        self.machine_id = machine_id
        self.rpm = rpm
        self.thermistor = thermistor
        self.fft = fft
        self.log = log
        self.decoder = BleVibrationDecoder(machine_id)
        self.bad_frames = 0
        self.temperature_count = 0
        self._json_buffer = b""
        self._json_noted = False
        self.window_count = 0
        self.out_dir = Path(out_dir) if out_dir else None
        if live_html and not self.out_dir:
            raise ValueError("live_html needs out_dir")
        self.live_html = live_html
        if self.out_dir:
            (self.out_dir / "windows").mkdir(parents=True, exist_ok=True)

    # -- temperature ------------------------------------------------------
    def on_temperature(self, data: bytes) -> dict[str, Any] | None:
        """Handle one notification on the temperature characteristic.

        Returns the last complete reading in this notification, or None.
        """
        raw = bytes(data)
        if self._json_buffer or raw.lstrip().startswith(b"{"):
            return self._on_json_piece(raw)
        try:
            reading = parse_thermistor_message(raw.decode("utf-8"), self.thermistor)
        except (ValueError, UnicodeDecodeError):
            self.log(f"Invalid temperature message: {raw!r}")
            return None
        self.temperature_count += 1
        reading["received_at_utc"] = _now()
        self.log(f"Temp  #{reading['sequence']:>5} | {reading['voltage_mv']:>4} mV | "
                 f"{reading['resistance_ohm'] / 1000:>7.2f} kΩ | {_fmt_temp(reading['temperature_c'])}")
        self._append_jsonl("temperature.jsonl", reading)
        return reading

    def _on_json_piece(self, raw: bytes) -> dict[str, Any] | None:
        """JSON status messages arrive in small pieces; rebuild them line by line."""
        self._json_buffer += raw
        last = None
        while b"\n" in self._json_buffer:
            line, self._json_buffer = self._json_buffer.split(b"\n", 1)
            if line.strip():
                last = self._on_json_message(line) or last
        if len(self._json_buffer) > MAX_JSON_BUFFER:
            self.log(f"Discarding {len(self._json_buffer)} bytes of unterminated JSON")
            self._json_buffer = b""
        return last

    def _on_json_message(self, line: bytes) -> dict[str, Any] | None:
        try:
            packet = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.log(f"Invalid JSON status message: {line[:80]!r}")
            return None
        if not self._json_noted:
            self._json_noted = True
            self.log("Note: board is sending JSON status messages (older firmware). "
                     "Temperature works; vibration needs the updated machine1 firmware.")

        last = None
        for sensor in packet.get("sensors", []) if isinstance(packet, dict) else []:
            if sensor.get("type") != "thermistor" or "voltage_mv" not in sensor:
                continue
            try:
                mv = float(sensor["voltage_mv"])
                resistance = thermistor_resistance(mv, self.thermistor)
                temp = voltage_to_celsius(mv, self.thermistor)
            except (TypeError, ValueError):
                resistance, temp = None, None
                mv = sensor.get("voltage_mv")
            reading = {
                "sequence": packet.get("timestamp_ms"),
                "machine_id": packet.get("machine_id"),
                "sensor_id": sensor.get("id"),
                "voltage_mv": mv,
                "resistance_ohm": resistance,
                "temperature_c": temp,
                "received_at_utc": _now(),
            }
            self.temperature_count += 1
            res_text = f"{resistance / 1000:>7.2f} kΩ" if resistance else "      ? kΩ"
            mv_text = f"{mv:g}" if isinstance(mv, float) else str(mv)
            self.log(f"Temp  t={packet.get('timestamp_ms')} ms | {sensor.get('id')} | "
                     f"{mv_text} mV | {res_text} | {_fmt_temp(temp)}")
            self._append_jsonl("temperature.jsonl", reading)
            last = reading
        return last

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
        if self.live_html:
            from ingest.fft_report import write_report
            try:
                write_report(self.out_dir, self.rpm, refresh_s=5)
            except Exception as err:  # a plotting problem must never stop data collection
                self.log(f"      report not updated: {err}")
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


def _fmt_temp(temp: float | None) -> str:
    return f"{temp:6.2f} °C" if temp is not None else "  wiring?"


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
    parser.add_argument("--live-html", action="store_true",
                        help="keep <out>/fft_report.html updated after every window (needs --out)")
    parser.add_argument("--simulate", action="store_true", help="fake board, no Bluetooth needed")
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    args = parser.parse_args(argv)

    if args.live_html and not args.out:
        parser.error("--live-html needs --out")
    handler = MachineGuardHandler(args.machine_id, rpm=args.rpm, out_dir=args.out, live_html=args.live_html)
    scanner_cls = client_cls = None
    if args.simulate:
        from simulation.fake_ble_board import FakeBoardClient, FakeBoardScanner
        scanner_cls, client_cls = FakeBoardScanner, FakeBoardClient
        handler.log("SIMULATION MODE: fake MachineGuard board\n")
    if args.live_html:
        handler.log(f"Live report: {args.out / 'fft_report.html'} (open it once; it refreshes itself)")
    try:
        asyncio.run(run(handler, scanner_cls, client_cls, args.duration))
    except KeyboardInterrupt:
        handler.log("\nStopped.\n" + handler.summary())
    except Exception as err:
        if type(err).__name__ == "BleakBluetoothNotAvailableError":
            handler.log("Bluetooth is off. Turn it on (Win + A, or Settings > Bluetooth & devices) and try again.")
        else:
            raise


if __name__ == "__main__":
    main()
