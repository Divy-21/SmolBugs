"""Records one run from the ESP32 over USB serial into data/raw.

    python -m ml.record --list-ports
    python -m ml.record --port COM5 --session 1 --label imbalance --seconds 60

The run number is picked automatically (next free one), so the file name is always
like  data/raw/S1_imbalance_03.csv  which is what train.py expects.

The ESP32 must print one line per sample at 921600 baud, 1000 lines per second:
    t_us,ax,ay,az,temp_c
    1234567,0.0123,-0.0456,1.0021,24.8
t_us from micros(), acceleration in g, temperature in °C (last thermistor reading).
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from .recordings import check_timing
from .settings import CSV_COLUMNS, FS, LABELS


def parse_line(raw: bytes) -> list[float] | None:
    """One sample, or None for boot messages, half lines and other junk."""
    parts = raw.decode("ascii", errors="ignore").strip().split(",")
    if len(parts) != len(CSV_COLUMNS):
        return None
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


def next_run_number(out: Path, session: int, label: str) -> int:
    taken = [int(p.stem.rsplit("_", 1)[1]) for p in out.glob(f"S{session}_{label}_*.csv")]
    return max(taken, default=0) + 1


def record(stream, seconds: float, path: Path) -> tuple[int, int, list[float]]:
    """Reads samples from stream (anything with readline) for `seconds`. Returns counts and timestamps."""
    good = bad = 0
    t_us: list[float] = []
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        stream.readline()  # first line is usually cut in half
        start = last_report = time.monotonic()
        while time.monotonic() - start < seconds:
            line = stream.readline()
            if not line:
                continue
            sample = parse_line(line)
            if sample is None:
                bad += 1
                continue
            writer.writerow([int(sample[0]), *sample[1:]])
            t_us.append(sample[0])
            good += 1
            now = time.monotonic()
            if now - last_report >= 1.0:
                print(f"\r  {now - start:4.0f}s  {good} samples  temp {sample[4]:.1f} °C   ", end="", flush=True)
                last_report = now
    print()
    return good, bad, t_us


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list-ports", action="store_true", help="show serial ports and exit")
    p.add_argument("--port", help="e.g. COM5 on Windows, /dev/ttyUSB0 on Linux")
    p.add_argument("--baud", type=int, default=921600)
    p.add_argument("--session", type=int)
    p.add_argument("--label", choices=LABELS)
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--out", default="data/raw")
    p.add_argument("--countdown", type=int, default=3, help="seconds to get ready before recording")
    args = p.parse_args()

    import serial  # pip install pyserial
    from serial.tools import list_ports

    if args.list_ports:
        for port in list_ports.comports():
            print(f"{port.device:<12} {port.description}")
        return
    if not (args.port and args.session and args.label):
        p.error("--port, --session and --label are required")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    run = next_run_number(out, args.session, args.label)
    path = out / f"S{args.session}_{args.label}_{run:02d}.csv"

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        for i in range(args.countdown, 0, -1):
            print(f"recording {path.name} in {i}...", flush=True)
            time.sleep(1)
        ser.reset_input_buffer()
        print(f"RECORDING {args.seconds:.0f}s")
        good, bad, t_us = record(ser, args.seconds, path)

    timing = check_timing(t_us, FS) if len(t_us) > 1 else {"dropped": 0, "gaps": 0, "actual_fs": 0.0}
    print(f"saved {path}: {good} samples, {bad} junk lines skipped")
    print(f"sample rate {timing['actual_fs']:.0f} Hz (want {FS:.0f}), dropped samples: {timing['dropped']}")
    if good < 0.9 * args.seconds * FS:
        print("WARNING: far fewer samples than expected. Check the baud rate and that the ESP32 prints every sample.")
    if timing["dropped"] > 0.01 * max(good, 1):
        print("WARNING: more than 1% of samples dropped. Re-record this run.")


if __name__ == "__main__":
    main()
