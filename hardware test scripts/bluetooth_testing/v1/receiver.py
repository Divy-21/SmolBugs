
import asyncio

from bleak import BleakClient, BleakScanner


DEVICE_NAME = "MachineGuard"

# Nordic UART Service TX characteristic
TX_CHARACTERISTIC_UUID = (
    "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
)


def notification_handler(sender, data):
    """Handle incoming BLE notifications."""

    message = data.decode("utf-8", errors="replace")

    print(f"Received: {message}")


async def main():

    print(f"Scanning for {DEVICE_NAME}...")

    devices = await BleakScanner.discover(timeout=10.0)

    target = None

    for device in devices:
        print(f"Found: {device.name} [{device.address}]")

        if device.name == DEVICE_NAME:
            target = device

    if target is None:
        print(f"\nCould not find {DEVICE_NAME}.")
        print("Make sure the Heltec is powered on and advertising.")
        return

    print(f"\nConnecting to {target.name}...")

    async with BleakClient(target) as client:

        print("Connected!")

        print("\nAvailable GATT services:")

        for service in client.services:
            print(f"Service: {service.uuid}")

            for characteristic in service.characteristics:
                print(
                    f"  Characteristic: {characteristic.uuid}"
                    f" | Properties: {characteristic.properties}"
                )

        print("\nSubscribing to notifications...")

        await client.start_notify(
            TX_CHARACTERISTIC_UUID,
            notification_handler
        )

        print("Listening for MachineGuard messages.")
        print("Press Ctrl+C to disconnect.\n")

        try:
            while True:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass

        finally:
            await client.stop_notify(
                TX_CHARACTERISTIC_UUID
            )


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nDisconnected.")

