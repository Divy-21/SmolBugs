import asyncio
import json

import numpy as np
import pytest

from ingest.ble_receiver import MachineGuardHandler, main, run
from processing.ble_protocol import window_to_frames
from simulation.fake_ble_board import FakeBoardClient, FakeBoardScanner
from simulation.packet_simulator import simulate_window_signals


def _quiet(**kw):
    lines = []
    return MachineGuardHandler(log=lines.append, **kw), lines


def test_temperature_message():
    h, lines = _quiet()
    r = h.on_temperature(bytearray(b"12,1582,69062"))
    assert r["sequence"] == 12 and r["temperature_c"] == pytest.approx(33.57, abs=0.05)
    assert "33.57 °C" in lines[-1]


def test_bad_temperature_message_logged_not_raised():
    h, lines = _quiet()
    assert h.on_temperature(bytearray(b"\xff\xfe")) is None
    assert h.on_temperature(bytearray(b"hello")) is None
    assert sum("Invalid temperature" in l for l in lines) == 2


def test_vibration_window_analysed_and_saved(tmp_path):
    h, lines = _quiet(rpm=3000, out_dir=tmp_path)
    h.on_temperature(b"1,1582,69062")
    results = [h.on_vibration(f) for f in window_to_frames(simulate_window_signals("imbalance"), window_id=3)]
    result = results[-1]
    assert all(r is None for r in results[:-1])
    assert result["axes"]["x"]["harmonics"][0]["amp_g"] == pytest.approx(0.30, rel=0.02)
    assert "1x=0.300" in lines[-1]

    vib = [json.loads(l) for l in (tmp_path / "vibration.jsonl").read_text().splitlines()]
    temp = [json.loads(l) for l in (tmp_path / "temperature.jsonl").read_text().splitlines()]
    assert vib[0]["window_id"] == 3 and temp[0]["sequence"] == 1
    saved = np.load(tmp_path / "windows" / "MG-001_w00003.npz")
    assert saved["accel_g"].shape == (4096, 3) and float(saved["sample_rate_hz"]) == 4000.0


def test_bad_vibration_frame_counted_not_raised():
    h, lines = _quiet()
    assert h.on_vibration(b"\x07") is None
    assert h.bad_frames == 1 and "Bad vibration frame" in lines[-1]
    assert "bad frames: 1" in h.summary()


def test_run_with_fake_board(monkeypatch):
    monkeypatch.setattr(FakeBoardClient, "window_interval_s", 0.05)
    monkeypatch.setattr(FakeBoardClient, "frame_gap_s", 0.0)
    h, lines = _quiet(rpm=3000)
    assert asyncio.run(run(h, FakeBoardScanner, FakeBoardClient, duration_s=1.2))
    assert h.window_count >= 4 and h.temperature_count >= 2
    assert h.bad_frames == 0 and h.decoder.stats.orphan_data_frames == 0
    assert any("MTU: 247" in l for l in lines)


def test_run_warns_on_small_mtu(monkeypatch):
    monkeypatch.setattr(FakeBoardClient, "mtu_size", 23)
    h, lines = _quiet()
    asyncio.run(run(h, FakeBoardScanner, FakeBoardClient, duration_s=0.2))
    assert any("WARNING: MTU 23" in l for l in lines)


def test_run_when_board_not_found():
    class NoScanner:
        @staticmethod
        async def find_device_by_name(name, timeout=15.0):
            return None

    h, lines = _quiet()
    assert asyncio.run(run(h, NoScanner, FakeBoardClient)) is False
    assert "not found" in lines[-1]


def test_cli_simulate(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(FakeBoardClient, "window_interval_s", 0.05)
    monkeypatch.setattr(FakeBoardClient, "frame_gap_s", 0.0)
    main(["--simulate", "--duration", "0.8", "--rpm", "3000", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert "SIMULATION MODE" in out and "Vib   window" in out
    assert (tmp_path / "vibration.jsonl").exists()


# --- JSON status messages, as sent by the board's current (older) firmware ----------------

def _json_message(ts, mv):
    return ('{"machine_id":"MG-001","timestamp_ms":%d,"sensors":[{"id":"sensor_0","type":"thermistor",'
            '"voltage_mv":%d},{"id":"sensor_1","type":"accelerometer","x_g":0.00,"y_g":0.00,"z_g":0.00}]}\n'
            % (ts, mv)).encode()


def _pieces(data, size=20):
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_json_split_into_20_byte_pieces_like_real_board(tmp_path):
    h, lines = _quiet(out_dir=tmp_path)
    stream = _json_message(30547, 1726) + _json_message(31048, 1728)
    pieces = _pieces(stream)
    assert pieces[0] == b'{"machine_id":"MG-00'      # exactly what the real log showed
    results = [h.on_temperature(bytearray(p)) for p in pieces]
    readings = [r for r in results if r]
    assert [r["sequence"] for r in readings] == [30547, 31048]
    assert readings[0]["voltage_mv"] == 1726
    assert readings[0]["resistance_ohm"] == pytest.approx(75_000 * 1726 / (3300 - 1726), rel=1e-9)
    from processing import voltage_to_celsius
    assert readings[0]["temperature_c"] == pytest.approx(voltage_to_celsius(1726), abs=1e-9)
    assert "1726 mV" in lines[1] and "29.47 °C" in lines[1]
    assert h.temperature_count == 2
    assert not any("Invalid" in l for l in lines)
    assert sum("older firmware" in l for l in lines) == 1
    saved = [json.loads(l) for l in (tmp_path / "temperature.jsonl").read_text().splitlines()]
    assert len(saved) == 2 and saved[1]["machine_id"] == "MG-001"


def test_json_pieces_of_any_size(tmp_path):
    h, _ = _quiet()
    stream = b"".join(_json_message(1000 + i, 1700 + i) for i in range(5))
    for size in (1, 7, 20, 244, len(stream)):
        h.temperature_count = 0
        for p in _pieces(stream, size):
            h.on_temperature(p)
        assert h.temperature_count == 5


def test_json_message_with_wiring_fault():
    h, lines = _quiet()
    r = h.on_temperature(_json_message(5, 0))
    assert r["temperature_c"] is None and "wiring?" in lines[-1]


def test_bad_json_line_logged_and_recovers():
    h, lines = _quiet()
    h.on_temperature(b'{"oops": \n')
    assert "Invalid JSON" in lines[-1]
    assert h.on_temperature(_json_message(9, 1730))["sequence"] == 9


def test_unterminated_json_buffer_is_capped():
    h, lines = _quiet()
    h.on_temperature(b"{" + b"x" * 9000)
    assert "Discarding" in lines[-1]
    assert h.on_temperature(b"7,1582,69062")["sequence"] == 7   # CSV still works afterwards


def test_report_reads_json_temperatures(tmp_path):
    from ingest.fft_report import build_report
    h, _ = _quiet(out_dir=tmp_path)
    for p in _pieces(_json_message(1, 1726) + _json_message(2, 1730)):
        h.on_temperature(p)
    temp = [t for t in build_report(tmp_path).data if t.name == "temperature"][0]
    assert len(temp.y) == 2


def test_lsm303d_status_messages_from_real_log(tmp_path):
    h, lines = _quiet(out_dir=tmp_path)
    for msg in (b"4,1260,-0.030,1.897,-1.142", b"24,1260,0.170,0.374,-0.795"):
        assert h.on_temperature(bytearray(msg)) is not None
    assert "accel  0.170  0.374 -0.795 g (total 0.89)" in lines[-1]
    assert not any("Invalid" in l for l in lines)
    saved = [json.loads(l) for l in (tmp_path / "temperature.jsonl").read_text().splitlines()]
    assert saved[1]["accel_g"] == [0.170, 0.374, -0.795]


# --- identity and cloud upload -------------------------------------------------------------

import http.server
import itertools
import math
import threading

from ingest.cloud import (
    STATUS_ENDPOINT,
    WINDOWS_ENDPOINT,
    CloudSender,
    IdentityTracker,
    PostResult,
    json_safe,
    make_packet_id,
)


def _boot_ids():
    counter = itertools.count()
    return lambda: f"boot{next(counter):04d}"


class FakeCloud:
    """Records every POST; responses can be scripted per call."""

    def __init__(self, responses=()):
        self.calls = []
        self.responses = list(responses)

    def __call__(self, url, body, headers, timeout):
        packets = json.loads(body)
        self.calls.append({"url": url, "headers": headers, "packets": packets})
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return PostResult(200, '{"ok": true}')

    def ids(self, call):
        return [p["packet_id"] for p in self.calls[call]["packets"]]


def _sender(post, **kw):
    lines = []
    kw.setdefault("sleep", lambda s: None)
    return CloudSender("https://cloud.example", "test-key", post=post, log=lines.append, **kw), lines


def test_same_packet_gets_same_packet_id_twice():
    a = make_packet_id("MG-001", "ab12cd34", "status", 7)
    assert a == make_packet_id("MG-001", "ab12cd34", "status", 7)
    assert len(a) == 32
    t1, t2 = IdentityTracker(lambda: "fixed"), IdentityTracker(lambda: "fixed")
    pkt = {"machine_id": "MG-001", "timestamp_ms": 500, "seq": 3}
    for t in (t1, t2):  # e.g. the same packet processed again after a restart with the same boot_id
        assert t.identify("status", "MG-001", clock=500, firmware_boot_id="ab12cd34",
                          firmware_seq=pkt["seq"]) == {"boot_id": "ab12cd34", "seq": 3,
                                                       "packet_id": make_packet_id("MG-001", "ab12cd34", "status", 3)}


def test_different_seq_boot_kind_or_machine_gives_different_packet_id():
    base = make_packet_id("MG-001", "b1", "status", 1)
    assert base != make_packet_id("MG-001", "b1", "status", 2)
    assert base != make_packet_id("MG-001", "b2", "status", 1)
    assert base != make_packet_id("MG-001", "b1", "window", 1)
    assert base != make_packet_id("MG-002", "b1", "status", 1)


def test_identical_sensor_values_still_get_different_ids():
    h, _ = _quiet()
    a = h.on_temperature(b"1,1582,0.0,0.0,1.0")
    b = h.on_temperature(b"2,1582,0.0,0.0,1.0")
    assert a["packet_id"] != b["packet_id"]


def test_gateway_seq_counts_from_zero_per_boot():
    t = IdentityTracker(_boot_ids())
    ids = [t.identify("status", "MG-001", clock=ms) for ms in (100, 600, 1100)]
    assert [i["seq"] for i in ids] == [0, 1, 2]
    assert {i["boot_id"] for i in ids} == {"boot0000"}


def test_timestamp_backwards_starts_new_boot_and_resets_seq():
    t = IdentityTracker(_boot_ids())
    before = [t.identify("status", "MG-001", clock=ms) for ms in (30_000, 30_500, 31_000)]
    after = [t.identify("status", "MG-001", clock=ms) for ms in (200, 700)]   # board rebooted
    assert {i["boot_id"] for i in before} == {"boot0000"}
    assert {i["boot_id"] for i in after} == {"boot0001"}
    assert [i["seq"] for i in after] == [0, 1]
    assert t.resets_detected == 1
    assert not {i["packet_id"] for i in before} & {i["packet_id"] for i in after}


def test_reset_is_per_machine():
    t = IdentityTracker(_boot_ids())
    a1 = t.identify("status", "MG-001", clock=5000)
    b1 = t.identify("status", "MG-002", clock=100)  # a different machine with a smaller clock
    a2 = t.identify("status", "MG-001", clock=5500)
    assert a1["boot_id"] == a2["boot_id"] != b1["boot_id"] and t.resets_detected == 0


def test_window_clock_does_not_reset_status_boot():
    # a window's first_sample_ms is older than status packets sent while it was uploading
    t = IdentityTracker(_boot_ids())
    s1 = t.identify("status", "MG-001", clock=40_000)
    w = t.identify("window", "MG-001", clock=37_000, firmware_seq=3, seq_wraps_at=65536)
    s2 = t.identify("status", "MG-001", clock=40_500)
    assert s1["boot_id"] == w["boot_id"] == s2["boot_id"] and t.resets_detected == 0


def test_firmware_boot_id_and_seq_are_used_when_present():
    t = IdentityTracker(_boot_ids())
    i = t.identify("status", "MG-001", clock=10, firmware_boot_id="fw000001", firmware_seq=42)
    assert (i["boot_id"], i["seq"]) == ("fw000001", 42)


def test_new_connection_gets_new_gateway_boot_id():
    t = IdentityTracker(_boot_ids())
    first = t.identify("status", "MG-001", clock=100)
    t.on_connect()
    second = t.identify("status", "MG-001", clock=600)
    assert first["boot_id"] != second["boot_id"] and second["seq"] == 0


def test_window_id_wraparound_keeps_ids_unique():
    t = IdentityTracker(_boot_ids())
    seqs = [t.identify("window", "MG-001", clock=ms, firmware_seq=wid, seq_wraps_at=65536)["seq"]
            for ms, wid in ((1_000, 65534), (11_000, 65535), (21_000, 0), (31_000, 1))]
    assert seqs == [65534, 65535, 65536, 65537]


def test_handler_tags_csv_json_and_windows(tmp_path):
    h, _ = _quiet(rpm=3000, out_dir=tmp_path, identity=IdentityTracker(_boot_ids()))
    csv = h.on_temperature(b"5,1582,0.1,0.2,0.9")
    js = None
    for p in _pieces(_json_message(30547, 1726)):
        js = h.on_temperature(p) or js
    win = None
    for f in window_to_frames(simulate_window_signals("healthy"), window_id=9, first_sample_ms=31_000):
        win = h.on_vibration(f) or win
    assert (csv["seq"], csv["boot_id"]) == (5, "boot0000")
    assert csv["packet_id"] == make_packet_id("MG-001", "boot0000", "status", 5)
    assert js["timestamp_ms"] == 30547 and js["seq"] == 0          # JSON packet has no seq: gateway counter
    assert (win["seq"], win["packet_id"]) == (9, make_packet_id("MG-001", "boot0000", "window", 9))
    assert win["temperature_c"] == pytest.approx(js["temperature_c"])
    saved = [json.loads(l) for l in (tmp_path / "vibration.jsonl").read_text().splitlines()]
    assert saved[0]["packet_id"] == win["packet_id"]


def test_handler_never_waits_or_raises_when_cloud_is_down():
    def down(url, body, headers, timeout):
        raise ConnectionError("no internet")

    sender, _ = _sender(down)
    h, _ = _quiet(cloud=sender)
    for i in range(5):
        assert h.on_temperature(f"{i},1582,0.0,0.0,1.0".encode()) is not None
    for f in window_to_frames(simulate_window_signals("healthy")):
        h.on_vibration(f)
    assert sender.backlog == 6 and sender.stats.requests == 0  # handler only queued


def test_failed_post_is_retried_with_same_ids():
    cloud = FakeCloud([PostResult(None, error="timed out"), PostResult(503, "busy"), PostResult(200, "{}")])
    sender, lines = _sender(cloud)
    h, _ = _quiet(cloud=sender)
    for i in range(3):
        h.on_temperature(f"{i},1582,0.0,0.0,1.0".encode())
    assert sender.flush_once() is False
    assert sender.flush_once() is False
    assert sender.flush_once() is True
    assert cloud.ids(0) == cloud.ids(1) == cloud.ids(2) and len(cloud.ids(0)) == 3
    assert sender.backlog == 0 and sender.stats.sent == 3 and sender.stats.retries == 2
    assert any("timed out" in l for l in lines) and any("HTTP 503" in l for l in lines)


def test_backoff_grows_and_resets():
    cloud = FakeCloud([PostResult(500)] * 4)
    sender, _ = _sender(cloud, max_backoff_s=5)
    sender.submit(STATUS_ENDPOINT, {"packet_id": "a"})
    backoffs = []
    for _ in range(4):
        sender.flush_once()
        backoffs.append(sender._backoff_s)
    assert backoffs == [1.0, 2.0, 4.0, 5.0]
    sender.flush_once()
    assert sender._backoff_s == 0.0


def test_batches_of_50_per_endpoint_in_order():
    cloud = FakeCloud()
    sender, _ = _sender(cloud)
    for i in range(120):
        sender.submit(STATUS_ENDPOINT, {"packet_id": f"s{i}"})
    sender.submit(WINDOWS_ENDPOINT, {"packet_id": "w0"})
    while sender.backlog:
        assert sender.flush_once()
    assert [len(c["packets"]) for c in cloud.calls] == [50, 50, 20, 1]
    assert [c["url"] for c in cloud.calls] == ["https://cloud.example/api/v1/status"] * 3 + [
        "https://cloud.example/api/v1/windows"]
    assert cloud.calls[0]["headers"]["x-device-key"] == "test-key"


def test_400_drops_only_the_bad_packet_and_logs_details():
    def post(url, body, headers, timeout):
        packets = json.loads(body)
        bad = [p for p in packets if p["packet_id"] == "bad"]
        if bad:
            return PostResult(400, json.dumps({"error": "invalid", "details": {"sensors": "required"}}))
        calls.append([p["packet_id"] for p in packets])
        return PostResult(200, "{}")

    calls = []
    sender, lines = _sender(post)
    for pid in ("a", "bad", "c"):
        sender.submit(STATUS_ENDPOINT, {"packet_id": pid})
    assert sender.flush_once()
    assert calls == [["a"], ["c"]] and sender.backlog == 0
    assert sender.stats.dropped_rejected == 1
    assert any('bad (400): {"sensors": "required"}' in l for l in lines)


def test_401_is_logged_clearly_and_packets_kept():
    sender, lines = _sender(FakeCloud([PostResult(401, '{"error": "unauthorized"}')]))
    sender.submit(STATUS_ENDPOINT, {"packet_id": "a"})
    assert sender.flush_once() is False
    assert sender.backlog == 1 and sender.stats.auth_failures == 1
    assert any("DEVICE_KEY" in l and "401" in l for l in lines)
    assert not any("test-key" in l for l in lines)   # the key itself is never logged


def test_backlog_is_capped_dropping_oldest():
    sender, lines = _sender(FakeCloud(), max_backlog=100)
    for i in range(250):
        sender.submit(STATUS_ENDPOINT, {"packet_id": f"p{i}"})
    assert sender.backlog == 100 and sender.stats.dropped_backlog == 150
    endpoint, batch = sender._take_batch()
    assert batch[0]["packet_id"] == "p150"                 # oldest were dropped
    assert any("dropped 1 oldest" in l for l in lines)
    assert any("dropped 100 oldest" in l for l in lines)
    assert sum("Cloud backlog full" in l for l in lines) == 2  # not one line per packet


def test_nan_and_numpy_become_valid_json():
    import numpy as np
    pkt = json_safe({"a": float("nan"), "b": [np.float64(1.5), float("inf")], "c": np.int64(3),
                     "d": {"e": -float("inf")}, "f": np.array([1.0, np.nan])})
    assert pkt == {"a": None, "b": [1.5, None], "c": 3, "d": {"e": None}, "f": [1.0, None]}
    json.dumps(pkt, allow_nan=False)


def test_window_with_nan_is_still_sent():
    cloud = FakeCloud()
    sender, _ = _sender(cloud)
    sender.submit(WINDOWS_ENDPOINT, {"packet_id": "w", "axes": {"x": {"kurtosis": float("nan")}}})
    assert sender.flush_once()
    assert cloud.calls[0]["packets"][0]["axes"]["x"]["kurtosis"] is None


def test_transport_exception_is_treated_as_network_failure():
    sender, lines = _sender(FakeCloud([RuntimeError("socket exploded")]))
    sender.submit(STATUS_ENDPOINT, {"packet_id": "a"})
    assert sender.flush_once() is False and sender.backlog == 1
    assert any("socket exploded" in l for l in lines)


def test_background_worker_sends_within_a_second():
    cloud = FakeCloud()
    sender, _ = _sender(cloud, flush_interval_s=0.1, sleep=None)
    sender.start()
    try:
        sender.submit(STATUS_ENDPOINT, {"packet_id": "a"})
        for _ in range(50):
            if cloud.calls:
                break
            threading.Event().wait(0.02)
        assert cloud.ids(0) == ["a"]
    finally:
        sender.close()


def test_close_flushes_remaining():
    cloud = FakeCloud()
    sender, _ = _sender(cloud)
    sender.start()
    for i in range(3):
        sender.submit(STATUS_ENDPOINT, {"packet_id": f"p{i}"})
    sender.close()
    assert sender.backlog == 0 and sender.stats.sent == 3


def test_cli_cloud_needs_env(monkeypatch):
    monkeypatch.delenv("CLOUD_URL", raising=False)
    monkeypatch.delenv("DEVICE_KEY", raising=False)
    with pytest.raises(SystemExit):
        main(["--simulate", "--cloud", "--duration", "0.1"])


class _Recorder(http.server.BaseHTTPRequestHandler):
    received: list = []
    keys: list = []
    fail_first = 0

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).keys.append(self.headers.get("x-device-key"))
        if type(self).fail_first > 0:
            type(self).fail_first -= 1
            self.send_response(503)
            self.end_headers()
            return
        type(self).received.append((self.path, json.loads(body)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"accepted": true}')

    def log_message(self, *args):
        pass


@pytest.fixture
def local_cloud():
    _Recorder.received, _Recorder.keys, _Recorder.fail_first = [], [], 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_cli_simulate_cloud_end_to_end_over_real_http(monkeypatch, capsys, local_cloud):
    monkeypatch.setenv("CLOUD_URL", local_cloud)
    monkeypatch.setenv("DEVICE_KEY", "secret-123")
    monkeypatch.setattr(FakeBoardClient, "window_interval_s", 0.05)
    monkeypatch.setattr(FakeBoardClient, "frame_gap_s", 0.0)
    _Recorder.fail_first = 1  # first POST fails with 503 and must be retried
    main(["--simulate", "--cloud", "--rpm", "3000", "--duration", "1.5"])
    out = capsys.readouterr().out
    assert "secret-123" not in out

    status = [p for path, batch in _Recorder.received if path == "/api/v1/status" for p in batch]
    windows = [p for path, batch in _Recorder.received if path == "/api/v1/windows" for p in batch]
    assert len(status) >= 2 and len(windows) >= 2
    assert set(_Recorder.keys) == {"secret-123"}
    ids = [p["packet_id"] for p in status + windows]
    assert len(ids) == len(set(ids))                       # retry produced no duplicates
    w = windows[0]
    assert {"boot_id", "seq", "packet_id", "received_at_utc", "temperature_c", "axes"} <= set(w)
    assert w["packet_id"] == make_packet_id("MG-001", w["boot_id"], "window", w["seq"])
    s = status[0]
    assert s["sensors"][0]["type"] == "thermistor" and "voltage_mv" in s["sensors"][0]
    assert "cloud: sent" in out


def test_backlog_after_outage_goes_up_in_full_batches():
    cloud = FakeCloud()
    sender, _ = _sender(cloud)
    for i in range(200):  # 100 s of status interleaved with a window every 10 s
        sender.submit(STATUS_ENDPOINT, {"packet_id": f"s{i}"})
        if i % 20 == 19:
            sender.submit(WINDOWS_ENDPOINT, {"packet_id": f"w{i // 20}"})
    while sender.backlog:
        assert sender.flush_once()
    sizes = [(c["url"].rsplit("/", 1)[1], len(c["packets"])) for c in cloud.calls]
    # oldest-first: after the first status batch, the oldest waiting packet is window w0
    assert sizes == [("status", 50), ("windows", 10), ("status", 50), ("status", 50), ("status", 50)]
    sent = [p["packet_id"] for c in cloud.calls for p in c["packets"]]
    assert [p for p in sent if p.startswith("s")] == [f"s{i}" for i in range(200)]  # order kept


def test_worker_catches_up_without_waiting_a_second_per_batch():
    cloud = FakeCloud()
    sender, _ = _sender(cloud, flush_interval_s=5.0, sleep=None)
    for i in range(160):
        sender.submit(STATUS_ENDPOINT, {"packet_id": f"s{i}"})  # 3rd batch-worth wakes the worker
    sender.start()
    try:
        for _ in range(100):
            if sender.backlog == 0:
                break
            threading.Event().wait(0.02)
        assert sender.backlog == 0 and len(cloud.calls) == 4
    finally:
        sender.close()
