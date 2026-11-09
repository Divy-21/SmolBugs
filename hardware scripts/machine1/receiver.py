"""Receive from the MachineGuard board and upload to the SmolBugs cloud. One command, no setup:

    python "hardware scripts/machine1/receiver.py"

This is a shortcut to the one real receiver, predictive-maintenance/ingest/ble_receiver.py
(Xavion's). It understands everything the machine1 firmware sends: the status line
every 0.5 s (temperature + accelerometer snapshot) and the vibration windows every 10 s,
which it runs through the FFT and uploads.

What the shortcut adds:
  - The first time, it asks for the device key (hidden while you paste) and saves it in
    smolbugs.env at the repo root. Git ignores that file. After that it just runs.
    The cloud address is built in. Environment variables CLOUD_URL / DEVICE_KEY still win.
  - Every run is saved to data/run-<date>-<time>/ unless you pass --out.
  - --simulate (fake board) uploads as MG-SIM, never as the real MG-001.
  - --no-cloud: print and save only.

--rpm defaults to 1200 (the drill). Any other option is passed through, e.g. --duration 60, --machine-id MG-002.
"""

import getpass
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "predictive-maintenance"))

from ingest.ble_receiver import main  # noqa: E402
from ingest.cloud import cloud_settings, save_setting, settings_file  # noqa: E402


def _ask_for_key() -> str:
    if not sys.stdin.isatty():
        return ""
    print("No device key saved on this laptop yet. Manraj DMs it (never post it in the channel).")
    try:
        key = getpass.getpass("Paste the device key (hidden), or press Enter to skip the upload: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    if key:
        path = save_setting("DEVICE_KEY", key)
        print(f"Saved to {path}. Git ignores it; you won't be asked again.\n")
    return key


def build_args(argv: list[str]) -> list[str]:
    args = list(argv)
    no_cloud = "--no-cloud" in args
    args = [a for a in args if a != "--no-cloud"]

    if "--rpm" not in args:
        args += ["--rpm", "1200"]  # the drill: 1200 rpm at the chuck (20 Hz); the motor shows at 60 Hz (3x)
    if "--simulate" in args and "--machine-id" not in args:
        args += ["--machine-id", "MG-SIM"]  # fake data must never land on the real machine
    if "--out" not in args:
        args += ["--out", str(REPO / "data" / datetime.now().strftime("run-%Y%m%d-%H%M%S"))]

    if no_cloud:
        print("Cloud upload OFF (--no-cloud).\n")
    elif "--cloud" not in args:
        url, key = cloud_settings()
        if not key:
            key = _ask_for_key()
        if key:
            args.append("--cloud")
        else:
            print(f"Cloud upload OFF: no device key. Run again and paste it, or put DEVICE_KEY=... in "
                  f"{settings_file()}\n")
    return args


if __name__ == "__main__":
    args = build_args(sys.argv[1:])
    out = args[args.index("--out") + 1]
    print(f"Saving this run to {out}")
    main(args)
