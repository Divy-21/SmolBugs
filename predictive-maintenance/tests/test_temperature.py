import math

import pytest

from processing import ThermistorConfig, parse_thermistor_message, temperatures_from_packet, voltage_to_celsius
from processing.temperature import thermistor_resistance

CFG = ThermistorConfig()


def _voltage_for_resistance(r, cfg=CFG):
    if cfg.ntc_on_low_side:
        return cfg.supply_mv * r / (r + cfg.fixed_resistor_ohm)
    return cfg.supply_mv * cfg.fixed_resistor_ohm / (r + cfg.fixed_resistor_ohm)


def test_defaults_match_machine1_circuit():
    assert CFG.fixed_resistor_ohm == 75_000 and CFG.supply_mv == 3300 and CFG.ntc_on_low_side


def test_r0_resistance_is_25c():
    assert voltage_to_celsius(_voltage_for_resistance(CFG.r0_ohm)) == pytest.approx(25.0, abs=1e-9)


def test_known_beta_point_zero_celsius():
    r0c = CFG.r0_ohm * math.exp(CFG.beta * (1 / 273.15 - 1 / 298.15))
    assert voltage_to_celsius(_voltage_for_resistance(r0c)) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("high_side", [False, True])
@pytest.mark.parametrize("r", [2_000.0, 10_000.0, 50_000.0])
def test_resistance_round_trip(r, high_side):
    cfg = ThermistorConfig(ntc_on_low_side=not high_side)
    assert thermistor_resistance(_voltage_for_resistance(r, cfg), cfg) == pytest.approx(r, rel=1e-9)


def test_hotter_means_lower_voltage_on_low_side():
    assert voltage_to_celsius(1200.0) > voltage_to_celsius(1800.0)


def test_current_packet_reading():
    # Real reading from MG-001: 1582 mV -> ~69 kΩ -> ~33.6 °C if the NTC is 100 kΩ / B3950
    assert thermistor_resistance(1582.0) == pytest.approx(69_064, rel=1e-3)
    assert voltage_to_celsius(1582.0) == pytest.approx(33.57, abs=0.05)


def test_resistance_matches_firmware_calculation():
    # machine1.ino: R = 75000 * V / (3.3 - V)
    v = 1.582
    assert thermistor_resistance(1582.0) == pytest.approx(75000 * v / (3.3 - v), rel=1e-9)


def test_parse_machine1_ble_message():
    r = parse_thermistor_message("44,1582,69062")
    assert (r["sequence"], r["voltage_mv"], r["resistance_ohm"]) == (44, 1582, 69062)
    assert r["temperature_c"] == pytest.approx(33.57, abs=0.05)


def test_parse_machine1_message_with_wiring_fault():
    assert parse_thermistor_message("5,0,0")["temperature_c"] is None


@pytest.mark.parametrize("bad", ["", "1,2", "a,b,c", "1,2,3,4,5,6"])
def test_parse_machine1_message_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_thermistor_message(bad)


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
    assert temps["sensor_0"] == pytest.approx(33.57, abs=0.05)
    assert temps["sensor_2"] is None
    assert "sensor_1" not in temps


def test_config_validation():
    with pytest.raises(ValueError):
        ThermistorConfig(beta=0)


def test_parse_lsm303d_status_message():
    r = parse_thermistor_message("24,1260,0.170,0.374,-0.795")
    assert (r["sequence"], r["voltage_mv"]) == (24, 1260)
    assert r["accel_g"] == [0.170, 0.374, -0.795]
    assert r["resistance_ohm"] == pytest.approx(75_000 * 1260 / (3300 - 1260), rel=1e-9)
    assert r["temperature_c"] == pytest.approx(voltage_to_celsius(1260), abs=1e-9)


def test_parse_lsm303d_status_with_wiring_fault():
    r = parse_thermistor_message("3,0,0.0,0.0,1.0")
    assert r["temperature_c"] is None and r["resistance_ohm"] is None and r["accel_g"] == [0.0, 0.0, 1.0]


@pytest.mark.parametrize("bad", ["1,2,3,4", "1,1260,a,b,c", "1,1260,nan,0,0", "1,1260,0,0,0,0"])
def test_parse_lsm303d_status_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_thermistor_message(bad)
