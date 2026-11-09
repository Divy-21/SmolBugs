"""Bluetooth receiver for the MachineGuard (machine1) board.

Subscribes to both characteristics:
  - status:      text 'sequence,voltage_mV,resistance_ohms' or 'sequence,voltage_mV,x_g,y_g,z_g'
                 every 500 ms, or newline-terminated
                 JSON status messages ({"machine_id", "timestamp_ms", "sensors": [...]}) that
                 arrive split across several notifications
  - vibration:   binary START/DATA frames (see processing/ble_protocol.py)

Run from the predictive-maintenance folder:
    python -m ingest.ble_receiver                    # real board
    python -m ingest.ble_receiver --simulate         # no hardware needed
    python -m ingest.ble_receiver --rpm 3000 --out data/session1
    python -m ingest.ble_receiver --rpm 3000 --out data/session1 --live-html
    python -m ingest.ble_receiver --cloud            # also upload (needs a device key; see ingest/cloud.py)
    python -m ingest.ble_receiver --relearn          # the model learns this drill's normal again (sensor moved)

Xavion's trained model (models/machineguard.joblib) runs on every window when it's there; see
ingest/live_model.py. --no-model turns it off.

Every status reading and analysed window carries boot_id, seq and packet_id (see ingest/cloud.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from processing import FFTConfig, ThermistorConfig, analyze_window, parse_thermistor_message
from processing.temperature import thermistor_resistance, voltage_to_celsius

from ingest.cloud import STATUS_ENDPOINT, WINDOWS_ENDPOINT, CloudSender, IdentityTracker, sender_from_env
from ingest.live_model import DEFAULT_MODEL, LiveModel
from processing.assembler import PacketError
from processing.ble_protocol import MIN_MTU, TEMPERATURE_CHAR_UUID, VIBRATION_CHAR_UUID, BleVibrationDecoder

DEVICE_NAME = "MachineGuard"
MAX_JSON_BUFFER = 8192  # bytes; guards against a message that never ends
RECONNECT_DELAY_S = 3.0  # wait before reconnecting after the Bluetooth link drops
NO_VIBRATION_WARN_S = 25.0  # the board sends a window every 10 s; say so if none has started by now


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
        cloud: CloudSender | None = None,
        identity: IdentityTracker | None = None,
        model: LiveModel | None = None,
    ):
        self.machine_id = machine_id
        self.rpm = rpm
        self.thermistor = thermistor
        self.fft = fft
        self.log = log
        self.decoder = BleVibrationDecoder(machine_id)
        self.bad_frames = 0
        self.temperature_count = 0
        self.cloud = cloud
        self.identity = identity or IdentityTracker()
        self.latest_temperature_c: dict[str, float] = {}
        self._json_buffer = b""
        self._json_noted = False
        self.window_count = 0
        self.model = model
        self._model_failed = False
        self.out_dir = Path(out_dir) if out_dir else None
        if live_html and not self.out_dir:
            raise ValueError("live_html needs out_dir")
        self.live_html = live_html
        if self.out_dir:
            (self.out_dir / "windows").mkdir(parents=True, exist_ok=True)

    def on_connect(self) -> None:
        """Called when the gateway (re)connects to the board."""
        self.identity.on_connect()

    def _submit(self, endpoint: str, packet: dict[str, Any]) -> None:
        if self.cloud is not None:
            self.cloud.submit(endpoint, packet)  # only queues; never waits on the network

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
        reading["machine_id"] = self.machine_id
        reading.update(self.identity.identify("status", self.machine_id, clock=reading["sequence"],
                                              firmware_seq=reading["sequence"]))
        if reading["temperature_c"] is not None:
            self.latest_temperature_c[self.machine_id] = reading["temperature_c"]
        self._submit(STATUS_ENDPOINT, _csv_status_packet(reading))
        res = reading["resistance_ohm"]
        line = (f"Temp  #{reading['sequence']:>5} | {reading['voltage_mv']:>4} mV | "
                f"{res / 1000 if res else 0:>7.2f} kΩ | {_fmt_temp(reading['temperature_c'])}")
        if "accel_g" in reading:
            x, y, z = reading["accel_g"]
            line += f" | accel {x:6.3f} {y:6.3f} {z:6.3f} g (total {(x*x + y*y + z*z) ** 0.5:.2f})"
        self.log(line)
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
        if not isinstance(packet, dict):
            self.log(f"Invalid JSON status message (not an object): {line[:80]!r}")
            return None
        if not self._json_noted:
            self._json_noted = True
            self.log("Note: board is sending JSON status messages (older firmware). "
                     "Temperature works; vibration needs the updated machine1 firmware.")

        machine_id = str(packet.get("machine_id") or self.machine_id)
        received = _now()
        ident = self.identity.identify(
            "status", machine_id, clock=packet.get("timestamp_ms"),
            firmware_boot_id=packet.get("boot_id"),
            firmware_seq=packet.get("seq", packet.get("sequence")),
        )
        cloud_packet = {**packet, "machine_id": machine_id, **ident, "received_at_utc": received,
                        "sensors": [dict(s) if isinstance(s, dict) else s for s in packet.get("sensors", [])]}

        last = None
        for index, sensor in enumerate(packet.get("sensors", [])):
            if not isinstance(sensor, dict):
                continue
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
                "timestamp_ms": packet.get("timestamp_ms"),
                "machine_id": machine_id,
                "sensor_id": sensor.get("id"),
                "voltage_mv": mv,
                "resistance_ohm": resistance,
                "temperature_c": temp,
                "received_at_utc": received,
                **ident,
            }
            cloud_packet["sensors"][index]["temperature_c"] = temp
            if temp is not None:
                self.latest_temperature_c[machine_id] = temp
            self.temperature_count += 1
            res_text = f"{resistance / 1000:>7.2f} kΩ" if resistance else "      ? kΩ"
            mv_text = f"{mv:g}" if isinstance(mv, float) else str(mv)
            self.log(f"Temp  t={packet.get('timestamp_ms')} ms | {sensor.get('id')} | "
                     f"{mv_text} mV | {res_text} | {_fmt_temp(temp)}")
            self._append_jsonl("temperature.jsonl", reading)
            last = reading
        self._submit(STATUS_ENDPOINT, cloud_packet)
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
        result["temperature_c"] = self.latest_temperature_c.get(window.machine_id)
        result.update(self.identity.identify("window", window.machine_id, clock=window.first_sample_ms,
                                             firmware_seq=window.window_id, seq_wraps_at=65536))
        x = result["axes"]["x"]
        line = (f"Vib   window {window.window_id:>5} | dominant {x['dominant_freq_hz']:7.2f} Hz "
                f"@ {x['dominant_amp_g']:.3f} g | velocity {x['velocity_rms_mm_s']:.2f} mm/s")
        if "harmonics" in x:
            line += " | " + " ".join(f"{h['order']}x={h['amp_g']:.3f}" for h in x["harmonics"])
        self.log(line)
        for warning in result["warnings"]:
            self.log(f"      warning: {warning}")
        ml = self._run_model(window, result)
        if ml is not None:
            result["ml"] = ml

        self._append_jsonl("vibration.jsonl", result)
        self._submit(WINDOWS_ENDPOINT, result)
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

    def _run_model(self, window, result: dict[str, Any]) -> dict[str, Any] | None:
        """The trained model's answer for this window. A model error turns the model off for the
        rest of the run; it never stops the receiver."""
        if self.model is None or self._model_failed:
            return None
        axes = list(result["axes"].values())
        velocity = max(a["velocity_rms_mm_s"] for a in axes)
        main_freq = max(axes, key=lambda a: a["dominant_amp_g"])["dominant_freq_hz"]  # as the cloud picks it
        try:
            ml = self.model.check(window.accel_g, window.sample_rate_hz, velocity, main_freq)
        except Exception as err:
            self._model_failed = True
            self.log(f"      model error, turning the model off for this run: {err!r}")
            return None
        if ml is not None:
            judged = ml["state"] in ("ok", "fault") and ml["confidence"] is not None
            sure = f", {ml['confidence']:.0%} sure" if judged else ""
            votes = f", {ml['agree']}/{ml['of']} agree" if ml["agree"] is not None else ""
            self.log(f"      model: {ml['message']}{sure}{votes}")
        return ml

    def summary(self) -> str:
        s = self.decoder.stats
        a = self.decoder.assembler.stats
        text = (f"temperature readings: {self.temperature_count} | windows: {self.window_count} | "
                f"vibration frames: {s.frames} | bad frames: {self.bad_frames} | "
                f"lost START frames: {s.orphan_data_frames} | incomplete windows dropped: {a.dropped_incomplete}")
        if self.identity.resets_detected:
            text += f" | board resets detected: {self.identity.resets_detected}"
        if self.cloud is not None:
            text += "\n" + self.cloud.summary()
        return text

    def _append_jsonl(self, name: str, record: dict[str, Any]) -> None:
        if self.out_dir:
            with open(self.out_dir / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")


def _csv_status_packet(reading: dict[str, Any]) -> dict[str, Any]:
    """Status packet in the cloud's shape for a CSV status message.

    The CSV format carries no timestamp_ms, so the packet has none.
    """
    sensors: list[dict[str, Any]] = [{
        "id": "sensor_0", "type": "thermistor", "voltage_mv": reading["voltage_mv"],
        "resistance_ohm": reading["resistance_ohm"], "temperature_c": reading["temperature_c"],
    }]
    if "accel_g" in reading:
        x, y, z = reading["accel_g"]
        sensors.append({"id": "sensor_1", "type": "accelerometer", "x_g": x, "y_g": y, "z_g": z})
    return {
        "machine_id": reading["machine_id"], "sensors": sensors,
        "boot_id": reading["boot_id"], "seq": reading["seq"], "packet_id": reading["packet_id"],
        "received_at_utc": reading["received_at_utc"],
    }


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
        handler.on_connect()
        if mtu is not None and mtu < MIN_MTU:
            handler.log(f"WARNING: MTU {mtu} < {MIN_MTU}; vibration frames will not fit and the "
                        "board will skip sending them.")

        await client.start_notify(TEMPERATURE_CHAR_UUID, lambda _s, d: handler.on_temperature(d))
        vibration_subscribed = True
        try:
            await client.start_notify(VIBRATION_CHAR_UUID, lambda _s, d: handler.on_vibration(d))
        except Exception as err:  # older firmware without the vibration characteristic
            vibration_subscribed = False
            handler.log(f"Vibration characteristic not available ({err}); temperature only.")

        handler.log("Receiving. Press Ctrl+C to stop.\n")
        started = time.monotonic()
        elapsed = 0.0
        frames_at_connect = handler.decoder.stats.frames
        warned_no_vibration = not vibration_subscribed
        while client.is_connected and (duration_s is None or elapsed < duration_s):
            await asyncio.sleep(0.2)
            elapsed = time.monotonic() - started  # the clock, not a count of sleeps (they can end early)
            if (not warned_no_vibration and elapsed >= NO_VIBRATION_WARN_S
                    and handler.decoder.stats.frames == frames_at_connect):
                warned_no_vibration = True
                handler.log(
                    f"WARNING: no vibration data after {NO_VIBRATION_WARN_S:.0f} s (the board should send a "
                    "window every 10 s). The board decides, and prints why on its USB serial output: "
                    "Arduino IDE > Serial Monitor at 115200 baud, look for 'Vibration skipped: MTU' or "
                    "'Vibration capture:'. Temperature readings are still being received.")
    handler.log("\n" + handler.summary())
    return True


def run_with_reconnect(handler: MachineGuardHandler, scanner_cls=None, client_cls=None,
                       duration_s: float | None = None, reconnect: bool = True,
                       delay_s: float = RECONNECT_DELAY_S) -> int:
    """run(), again and again: if the Bluetooth link drops (or the board isn't found), wait
    delay_s and connect again, until duration_s has passed or Ctrl+C. Returns how many times it
    connected. Bluetooth being switched off still stops it (that needs a person)."""
    start = time.monotonic()
    connections = 0
    while True:
        remaining = None if duration_s is None else duration_s - (time.monotonic() - start)
        if remaining is not None and remaining <= 0:
            break
        try:
            found = asyncio.run(run(handler, scanner_cls, client_cls, remaining))
        except Exception as err:
            if type(err).__name__ == "BleakBluetoothNotAvailableError":
                raise
            handler.log(f"Bluetooth error: {err}")
            found = False
        connections += found
        if not reconnect or (duration_s is not None and time.monotonic() - start >= duration_s):
            break
        what = "Connection lost" if found else "Board not found"
        handler.log(f"{what}; trying again in {delay_s:.0f} s (Ctrl+C to stop).")
        time.sleep(delay_s)
    return connections


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SmolBugs Bluetooth receiver")
    parser.add_argument("--machine-id", default="MG-001")
    parser.add_argument("--rpm", type=float, help="machine speed, enables 1x/2x/3x harmonics")
    parser.add_argument("--out", type=Path, help="folder to save temperature, results and raw windows")
    parser.add_argument("--live-html", action="store_true",
                        help="keep <out>/fft_report.html updated after every window (needs --out)")
    parser.add_argument("--simulate", action="store_true", help="fake board, no Bluetooth needed")
    parser.add_argument("--cloud", action="store_true",
                        help="also upload to the SmolBugs cloud (device key from DEVICE_KEY or smolbugs.env)")
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    parser.add_argument("--no-reconnect", action="store_true",
                        help="exit when the Bluetooth link drops (default: reconnect until Ctrl+C)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="trained model to run on every window")
    parser.add_argument("--no-model", action="store_true", help="don't run the trained model")
    parser.add_argument("--relearn", action="store_true",
                        help="forget this machine's saved normal and learn it again (after moving the sensor)")
    args = parser.parse_args(argv)

    if args.live_html and not args.out:
        parser.error("--live-html needs --out")
    cloud = None
    if args.cloud:
        try:
            cloud = sender_from_env()
        except ValueError as err:
            parser.error(str(err))
    model = None if args.no_model else LiveModel.load(args.model, args.machine_id, relearn=args.relearn)
    handler = MachineGuardHandler(args.machine_id, rpm=args.rpm, out_dir=args.out, live_html=args.live_html,
                                  cloud=cloud, model=model)
    if cloud is not None:
        handler.log(f"Cloud upload: {cloud.base_url}")
        cloud.start()
    scanner_cls = client_cls = None
    if args.simulate:
        from simulation.fake_ble_board import FakeBoardClient, FakeBoardScanner
        scanner_cls, client_cls = FakeBoardScanner, FakeBoardClient
        handler.log("SIMULATION MODE: fake MachineGuard board\n")
    if args.live_html:
        handler.log(f"Live report: {args.out / 'fft_report.html'} (open it once; it refreshes itself)")
    try:
        run_with_reconnect(handler, scanner_cls, client_cls, args.duration, reconnect=not args.no_reconnect)
    except KeyboardInterrupt:
        if cloud is not None:
            cloud.close()
            cloud = None
        handler.log("\nStopped.\n" + handler.summary())
    except Exception as err:
        if type(err).__name__ == "BleakBluetoothNotAvailableError":
            handler.log("Bluetooth is off. Turn it on (Win + A, or Settings > Bluetooth & devices) and try again.")
        else:
            raise
    finally:
        if cloud is not None:
            cloud.close()
            handler.log("After final upload, " + cloud.summary())


if __name__ == "__main__":
    main()
