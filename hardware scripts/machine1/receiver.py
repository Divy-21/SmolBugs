
import asyncio

from bleak import BleakClient, BleakScanner


DEVICE_NAME = "MachineGuard"

TX_CHARACTERISTIC_UUID = (
    "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
)


def notification_handler(sender, data):

    try:
        message = data.decode("utf-8")

        # Expected format:
        # sequence,voltage_mV,resistance_ohms

        sequence, voltage_mv, resistance_ohms = (
            message.split(",")
        )

        voltage_mv = int(voltage_mv)
        resistance_ohms = int(resistance_ohms)

        print(
            f"Packet: {sequence:>5} | "
            f"Voltage: {voltage_mv:>4} mV | "
            f"Resistance: {resistance_ohms / 1000:>7.2f} kOhm"
        )

    except (ValueError, UnicodeDecodeError):
        print(f"Invalid packet received: {data!r}")


async def main():

    print(f"Scanning for {DEVICE_NAME}...")

    device = await BleakScanner.find_device_by_name(
        DEVICE_NAME,
        timeout=15.0
    )

    if device is None:

        print("MachineGuard not found.")

        print("Check that the Heltec is powered on and advertising.")

        return

    print(f"Found: {device.name} [{device.address}]")

    print("Connecting...")

    async with BleakClient(device) as client:

        print("Connected to MachineGuard.")

        await client.start_notify(
            TX_CHARACTERISTIC_UUID,
            notification_handler
        )

        print()
        print("Receiving thermistor measurements...")
        print("--------------------------------------")
        print("Press Ctrl+C to disconnect.")
        print()

        while True:

            await asyncio.sleep(1)


if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("\nDisconnected.")

