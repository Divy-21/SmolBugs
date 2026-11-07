import math

import pytest

from processing import ThermistorConfig, temperatures_from_packet, voltage_to_celsius
from processing.temperature import thermistor_resistance

CFG = ThermistorConfig()


def _voltage_for_resistance(r, cfg=CFG):
    if cfg.ntc_on_low_side:
        return cfg.supply_mv * r / (r + cfg.fixed_resistor_ohm)
    return cfg.supply_mv * cfg.fixed_resistor_ohm / (r + cfg.fixed_resistor_ohm)


def test_midpoint_is_25c():
    assert voltage_to_celsius(1650.0) == pytest.approx(25.0, abs=1e-9)


def test_known_beta_point_zero_celsius():
    r0c = 10_000 * math.exp(3950 * (1 / 273.15 - 1 / 298.15))  # ~33.6 kΩ
    assert voltage_to_celsius(_voltage_for_resistance(r0c)) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("high_side", [False, True])
@pytest.mark.parametrize("r", [2_000.0, 10_000.0, 50_000.0])
def test_resistance_round_trip(r, high_side):
    cfg = ThermistorConfig(ntc_on_low_side=not high_side)
    assert thermistor_resistance(_voltage_for_resistance(r, cfg), cfg) == pytest.approx(r, rel=1e-9)


def test_hotter_means_lower_voltage_on_low_side():
    assert voltage_to_celsius(1200.0) > voltage_to_celsius(1800.0)


def test_current_packet_reading():
    # The real packet from MG-001: 1582 mV -> about 27 °C with default assumptions
    assert voltage_to_celsius(1582.0) == pytest.approx(26.87, abs=0.05)


@pytest.mark.parametrize("mv", [0.0, 3300.0, -5.0, 4000.0])
def test_open_or_short_circuit_rejected(mv):
    with pytest.raises(ValueError, match="wiring"):
        voltage_to_celsius(mv)


def test_temperatures_from_current_packet_format():
    packet = {
        "machine_id": "MG-001",
        "timestamp_ms": 96847,
        "sensors": [
            {"id": "sensor_0", "type": "thermistor", "voltage_mv": 1582},
            {"id": "sensor_1", "type": "accelerometer", "x_g": 0.0, "y_g": 0.0, "z_g": 0.0},
            {"id": "sensor_2", "type": "thermistor", "voltage_mv": 0},
        ],
        "received_at_utc": "2026-11-07T17:58:52.177715+00:00",
    }
    temps = temperatures_from_packet(packet)
    assert temps["sensor_0"] == pytest.approx(26.87, abs=0.05)
    assert temps["sensor_2"] is None
    assert "sensor_1" not in temps


def test_config_validation():
    with pytest.raises(ValueError):
        ThermistorConfig(beta=0)
