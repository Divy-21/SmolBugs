"""Upload runs that were recorded without --cloud (ml.record, or the receiver with only --out).

Run from the predictive-maintenance folder:
    python -m ingest.upload_run data/raw                      # every run folder under data/raw
    python -m ingest.upload_run data/raw/S1_normal_free_01    # one run
    python -m ingest.upload_run data/raw --dry-run            # count what would be sent, send nothing

A run folder is what the receiver's --out writes: temperature.jsonl (one status reading per
line) and vibration.jsonl (one analysed window per line). Raw windows/*.npz stay on the laptop.

The device key comes from DEVICE_KEY or smolbugs.env at the repo root, as for --cloud. Each
reading keeps the packet_id it got when it was recorded, so uploading the same run twice is
safe: the cloud skips what it already has. Readings keep their original times
(received_at_utc), so they appear in the dashboard's history at the time they were recorded.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from ingest.ble_receiver import _csv_status_packet
from ingest.cloud import STATUS_ENDPOINT, WINDOWS_ENDPOINT, CloudSender, make_packet_id, sender_from_env

MAX_FAILED_TRIES = 8  # consecutive failed batches before giving up on a run


def run_folders(paths: list[Path]) -> list[Path]:
    """The run folders in `paths`: each path is a run folder, or a folder of run folders."""
    found: list[Path] = []
    for p in paths:
        if not p.is_dir():
            print(f"skipped {p}: not a folder")
            continue
        if _is_run(p):
            found.append(p)
        else:
            found.extend(sorted(c for c in p.iterdir() if c.is_dir() and _is_run(c)))
    return found


def _is_run(folder: Path) -> bool:
    return (folder / "temperature.jsonl").exists() or (folder / "vibration.jsonl").exists()


def _lines(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                print(f"  {path.name} line {n}: not valid JSON, skipped")


def status_packets(run: Path, machine_id: str | None) -> list[dict[str, Any]]:
    packets = []
    for i, reading in enumerate(_lines(run / "temperature.jsonl")):
        if machine_id:
            reading["machine_id"] = machine_id
        reading.setdefault("machine_id", "MG-001")
        if "packet_id" not in reading:  # recorded by a receiver older than packet IDs
            seq = int(reading.get("sequence", i))
            reading.update(boot_id=f"run-{run.name}"[:64], seq=seq,
                           packet_id=make_packet_id(reading["machine_id"], f"run-{run.name}", "status", seq))
        packets.append(_csv_status_packet(reading) if "voltage_mv" in reading else reading)
    return packets


def window_packets(run: Path, machine_id: str | None) -> list[dict[str, Any]]:
    packets = []
    for i, result in enumerate(_lines(run / "vibration.jsonl")):
        if machine_id:
            result["machine_id"] = machine_id
        if "packet_id" not in result:
            seq = int(result.get("window_id", i))
            result.update(boot_id=f"run-{run.name}"[:64], seq=seq,
                          packet_id=make_packet_id(result.get("machine_id", "MG-001"), f"run-{run.name}", "window", seq))
        packets.append(result)
    return packets


def drain(sender: CloudSender) -> bool:
    """Send everything queued, batch by batch. False if the cloud kept failing."""
    failures = 0
    while sender.backlog:
        if sender.flush_once():
            failures = 0
            continue
        failures += 1
        if failures >= MAX_FAILED_TRIES:
            return False
        time.sleep(min(2 ** failures, 15))
    return True


def main(argv: list[str] | None = None, sender: CloudSender | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+", type=Path, help="run folders, or folders containing them (e.g. data/raw)")
    p.add_argument("--machine-id", help="upload as this machine instead of the one recorded (default: as recorded)")
    p.add_argument("--dry-run", action="store_true", help="count what would be sent; send nothing")
    args = p.parse_args(argv)

    runs = run_folders(args.paths)
    if not runs:
        print("No run folders found (a run folder has temperature.jsonl or vibration.jsonl).")
        return 1
    if sender is None and not args.dry_run:
        try:
            sender = sender_from_env(max_backlog=1_000_000)
        except ValueError as err:
            print(f"Can't upload: {err}")
            return 1
        print(f"Uploading to {sender.base_url}")

    ok = True
    for run in runs:
        status = status_packets(run, args.machine_id)
        windows = window_packets(run, args.machine_id)
        print(f"{run.name}: {len(status)} status readings, {len(windows)} vibration windows")
        if args.dry_run or sender is None:
            continue
        for pkt in status:
            sender.submit(STATUS_ENDPOINT, pkt)
        for pkt in windows:
            sender.submit(WINDOWS_ENDPOINT, pkt)
        if not drain(sender):
            print(f"  stopped: the cloud kept failing ({sender.summary()})")
            ok = False
            break
    if sender is not None:
        print(sender.summary())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
