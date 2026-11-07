
import asyncio
import json

from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner


DEVICE_NAME = "MachineGuard"

TX_CHARACTERISTIC_UUID = (
    "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
)

# Holds incomplete JSON messages between BLE notifications.
receive_buffer = bytearray()


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

        await client.start_notify(
            TX_CHARACTERISTIC_UUID,
            notification_handler
        )

        print()
        print("Receiving MachineGuard JSON telemetry...")
        print("Press Ctrl+C to disconnect.")
        print()

        while True:

            await asyncio.sleep(1)


if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("\nDisconnected.")

