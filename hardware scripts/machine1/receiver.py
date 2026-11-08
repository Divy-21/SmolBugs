"""Receive from the MachineGuard board and (optionally) upload to the SmolBugs cloud.

This is a shortcut to the one real receiver, predictive-maintenance/ingest/ble_receiver.py
(Xavion's). It understands everything the machine1 firmware sends: the status line
every 0.5 s (temperature + accelerometer snapshot) and the vibration windows every 10 s,
which it runs through the FFT. An older version of this file only understood JSON
lines, so with the current firmware it printed errors and nothing reached the cloud.

    python "hardware scripts/machine1/receiver.py"                     # print only
    python "hardware scripts/machine1/receiver.py" --out data/run1     # also save the session

Upload is on automatically when CLOUD_URL and DEVICE_KEY are set (see docs/sending-data.md).
Any other option is passed through, e.g. --rpm 3000, --simulate, --duration 60.
"""

import os
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[2] / "predictive-maintenance"
sys.path.insert(0, str(PIPELINE))

from ingest.ble_receiver import main  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    if os.environ.get("CLOUD_URL") and os.environ.get("DEVICE_KEY") and "--cloud" not in args:
        args.append("--cloud")
    elif "--cloud" not in args:
        print("Cloud upload OFF: set CLOUD_URL and DEVICE_KEY to send readings to the cloud.\n")
    main(args)
