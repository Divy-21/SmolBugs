# Hardware Plan

## Project Overview

This project is a prototype machine-condition monitoring system for
detecting abnormal operating conditions associated with potential
equipment failure. The prototype focuses on two measurable indicators:

-   **Excessive vibration**, measured with an external accelerometer.
-   **Abnormal temperature rise**, measured with a contact thermistor.

The system will collect sensor readings, calculate basic condition
metrics, and provide local status feedback. It is intended to
demonstrate early anomaly detection, not to diagnose a specific
mechanical failure.

## Hardware

  -----------------------------------------------------------------------
  Component                           Role
  ----------------------------------- -----------------------------------
  **Heltec WiFi LoRa 32 V3            Main controller; reads sensors and
  (ESP32-S3)**                        processes measurements

  **Onboard 0.96-inch OLED**          Displays live readings and system
                                      status or warnings

  **Adafruit ICM-20948 IMU**          Supplies three-axis acceleration
                                      data for vibration monitoring

  **NTC thermistor**                  Measures the temperature of the
                                      monitored equipment or test surface

  **Fixed resistor**                  Forms a voltage divider with the
                                      thermistor so the ESP32 can read
                                      temperature through an ADC input

  **Breadboard, jumper wires and      Support prototype wiring and sensor
  power connections**                 integration
  -----------------------------------------------------------------------

The Heltec board does not have a built-in accelerometer, so the
ICM-20948 is an external sensor. Its I²C interface will be used to
communicate with the ESP32. The OLED uses the board's onboard display
connection.

## System Design

1.  The ICM-20948 samples acceleration along the X, Y and Z axes.
2.  The firmware processes those samples to estimate vibration
    magnitude, including an RMS vibration metric over a sampling window.
3.  The thermistor and fixed resistor form a voltage divider. The ESP32
    reads the divider voltage through an ADC input and converts it to an
    estimated temperature using the thermistor's characteristics.
4.  The ESP32 combines the vibration and temperature measurements to
    determine whether the monitored equipment is operating within the
    prototype's configured limits.
5.  The OLED presents the current readings and a clear normal or warning
    state.

Wi-Fi may be used to transmit readings to a dashboard if time permits.
LoRa is available on the Heltec board but is not a core part of the
current prototype.

## Development and Validation

Sensors will be tested independently before full integration:

-   Verify the OLED and ESP32 startup.
-   Confirm I²C communication and stable acceleration readings from the
    ICM-20948.
-   Validate the thermistor voltage-divider readings against known or
    independently measured temperatures.
-   Integrate the sensors and check that readings update consistently.
-   Test the warning logic by introducing controlled vibration and
    temperature changes.

The thermistor's exact type and calibration parameters must be confirmed
before relying on its temperature output. Thresholds will be established
through prototype testing rather than treated as universal equipment
limits.

## Scope

The competition prototype will demonstrate sensor acquisition, basic
signal processing, and threshold-based anomaly alerts. It will not claim
predictive maintenance accuracy, identify every failure mode, or replace
industrial condition-monitoring equipment.
