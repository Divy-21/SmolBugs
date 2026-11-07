
import asyncio
import json
import os
import sys

from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner

# Cloud upload reuses Xavion's tested uploader (predictive-maintenance/ingest/cloud.py):
# it adds boot_id / seq / packet_id, batches, retries, and never blocks this script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "predictive-maintenance"))
from ingest.cloud import IdentityTracker, STATUS_ENDPOINT, sender_from_env  # noqa: E402


DEVICE_NAME = "MachineGuard"

TX_CHARACTERISTIC_UUID = (
    "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
)

# Holds incomplete JSON messages between BLE notifications.
receive_buffer = bytearray()

# Uploads to the cloud when CLOUD_URL and DEVICE_KEY are set; prints only otherwise.
identity = IdentityTracker()
cloud = None


def notification_handler(sender, data):

    global receive_buffer

    # Append the incoming BLE chunk.
    receive_buffer.extend(data)

    # Messages end with a newline.
    while b"\n" in receive_buffer:

        message, _, remainder = receive_buffer.partition(b"\n")

        receive_buffer = bytearray(remainder)

        if not message:
            continue

        try:

            # Reconstruct and parse JSON.
            payload = json.loads(message.decode("utf-8"))

            # Add laptop reception timestamp.
            payload["received_at_utc"] = (
                datetime.now(timezone.utc).isoformat()
            )

            if cloud is not None and isinstance(payload, dict):
                # Stable ID for this reading, so a retried upload isn't stored twice.
                payload.update(identity.identify(
                    "status",
                    payload.get("machine_id", "MG-001"),
                    clock=payload.get("timestamp_ms"),
                    firmware_boot_id=payload.get("boot_id"),
                    firmware_seq=payload.get("seq"),
                ))
                cloud.submit(STATUS_ENDPOINT, payload)  # only queues; never waits on the network

            print(json.dumps(payload, indent=2))

            print("-" * 50)

        except (json.JSONDecodeError, UnicodeDecodeError) as error:

            print(f"Invalid JSON received: {error}")


async def main():

    print(f"Scanning for {DEVICE_NAME}...")

    device = await BleakScanner.find_device_by_name(
        DEVICE_NAME,
        timeout=15.0
    )

    if device is None:

        print("MachineGuard not found.")

        return

    print(f"Found: {device.name} [{device.address}]")

    print("Connecting...")

    async with BleakClient(device) as client:

        print("Connected to MachineGuard.")

        print(f"Negotiated MTU reported by Bleak: {client.mtu_size}")

        identity.on_connect()

        await client.start_notify(
            TX_CHARACTERISTIC_UUID,
            notification_handler
        )

        print()
        print("Receiving MachineGuard JSON telemetry...")
        print("Press Ctrl+C to disconnect.")
        print()

        seconds = 0

        while True:

            await asyncio.sleep(1)

            seconds += 1

            # Every 10 s, show whether uploads are getting through.
            if cloud is not None and seconds % 10 == 0:
                print(cloud.summary())


def start_cloud():

    global cloud

    if not (os.environ.get("CLOUD_URL") and os.environ.get("DEVICE_KEY")):
        print("Cloud upload OFF: set CLOUD_URL and DEVICE_KEY to send readings to the cloud.")
        return

    cloud = sender_from_env().start()
    print(f"Cloud upload ON: {cloud.base_url}")


if __name__ == "__main__":

    start_cloud()

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("\nDisconnected.")

    finally:

        if cloud is not None:
            cloud.close()  # sends what's still waiting (up to 5 s)
            print("After final upload, " + cloud.summary())
