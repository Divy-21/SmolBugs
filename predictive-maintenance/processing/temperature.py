"""Converts the thermistor's divider voltage (voltage_mv in the current packets) to °C.

Uses the Beta equation for an NTC thermistor.

Circuit (from machine1.ino): 3.3 V -> 75 kΩ fixed resistor -> ADC pin -> NTC -> GND.
The NTC itself (100 kΩ, Beta 3950) is an ASSUMPTION until checked against the part's
datasheet: a wrong value still gives a plausible-looking but wrong temperature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

KELVIN = 273.15


@dataclass(frozen=True)
class ThermistorConfig:
    supply_mv: float = 3300.0
    fixed_resistor_ohm: float = 75_000.0  # confirmed in machine1.ino
    r0_ohm: float = 100_000.0  # UNVERIFIED: NTC resistance at t0_c
    t0_c: float = 25.0
    beta: float = 3950.0  # UNVERIFIED
    ntc_on_low_side: bool = True  # NTC between ADC pin and GND

    def __post_init__(self) -> None:
        if min(self.supply_mv, self.fixed_resistor_ohm, self.r0_ohm, self.beta) <= 0:
            raise ValueError("supply, resistors and beta must be positive")


def thermistor_resistance(voltage_mv: float, cfg: ThermistorConfig = ThermistorConfig()) -> float:
    """NTC resistance from the measured divider voltage."""
    if not 0 < voltage_mv < cfg.supply_mv:
        # 0 or full supply means a short or an open circuit, not a temperature.
        raise ValueError(f"voltage {voltage_mv} mV outside (0, {cfg.supply_mv}) mV: check thermistor wiring")
    if cfg.ntc_on_low_side:
        return cfg.fixed_resistor_ohm * voltage_mv / (cfg.supply_mv - voltage_mv)
    return cfg.fixed_resistor_ohm * (cfg.supply_mv - voltage_mv) / voltage_mv


def voltage_to_celsius(voltage_mv: float, cfg: ThermistorConfig = ThermistorConfig()) -> float:
    r = thermistor_resistance(voltage_mv, cfg)
    inv_t = 1.0 / (cfg.t0_c + KELVIN) + math.log(r / cfg.r0_ohm) / cfg.beta
    return 1.0 / inv_t - KELVIN


def temperatures_from_packet(packet: dict[str, Any], cfg: ThermistorConfig = ThermistorConfig()) -> dict[str, float | None]:
    """{sensor_id: °C} for each thermistor in a status packet; None if the reading is invalid."""
    out: dict[str, float | None] = {}
    for sensor in packet.get("sensors", []):
        if sensor.get("type") != "thermistor" or "voltage_mv" not in sensor:
            continue
        try:
            out[str(sensor.get("id"))] = voltage_to_celsius(float(sensor["voltage_mv"]), cfg)
        except (TypeError, ValueError):
            out[str(sensor.get("id"))] = None
    return out


def parse_thermistor_message(text: str, cfg: ThermistorConfig = ThermistorConfig()) -> dict[str, Any]:
    """Parse a machine1 BLE status message. Two formats are accepted:

    'sequence,voltage_mV,resistance_ohms'          (thermistor-only firmware)
    'sequence,voltage_mV,x_g,y_g,z_g'              (firmware with the LSM303D)
    """
    parts = text.strip().split(",")
    if len(parts) not in (3, 5):
        raise ValueError(f"expected 3 or 5 comma-separated fields, got {text!r}")
    sequence, voltage_mv = int(parts[0]), int(parts[1])
    accel = None
    if len(parts) == 3:
        resistance: float | None = int(parts[2])
    else:
        accel = [float(p) for p in parts[2:]]
        if not all(math.isfinite(a) for a in accel):
            raise ValueError(f"non-finite acceleration in {text!r}")
        try:
            resistance = thermistor_resistance(voltage_mv, cfg)
        except ValueError:
            resistance = None
    try:
        temperature = voltage_to_celsius(voltage_mv, cfg)
    except ValueError:
        temperature = None
    reading: dict[str, Any] = {"sequence": sequence, "voltage_mv": voltage_mv,
                               "resistance_ohm": resistance, "temperature_c": temperature}
    if accel is not None:
        reading["accel_g"] = accel
    return reading
