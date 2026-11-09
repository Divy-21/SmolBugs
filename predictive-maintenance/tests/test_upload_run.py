import json

from ingest.cloud import STATUS_ENDPOINT, WINDOWS_ENDPOINT
from ingest.upload_run import main, run_folders, status_packets


def _run(folder, with_ids=True):
    folder.mkdir(parents=True)
    reading = {"sequence": 7, "voltage_mv": 1582, "resistance_ohm": 69000, "temperature_c": 33.6,
               "accel_g": [0.1, -0.2, -0.86], "received_at_utc": "2026-11-08T23:00:00+00:00",
               "machine_id": "MG-001"}
    if with_ids:
        reading.update(boot_id="b1", seq=7, packet_id="p" * 32)
    (folder / "temperature.jsonl").write_text(json.dumps(reading) + "\n", encoding="utf-8")
    window = {"machine_id": "MG-001", "window_id": 3, "packet_id": "w" * 32, "axes": {}}
    (folder / "vibration.jsonl").write_text(json.dumps(window) + "\n\n", encoding="utf-8")


class FakeSender:
    def __init__(self):
        self.sent = []
        self.backlog = 0
        self.base_url = "fake"

    def submit(self, endpoint, packet):
        self.sent.append((endpoint, packet))

    def flush_once(self):
        return True

    def summary(self):
        return f"sent {len(self.sent)}"


def test_finds_runs_in_a_parent_folder_or_directly(tmp_path):
    _run(tmp_path / "raw" / "S1_normal_free_01")
    _run(tmp_path / "raw" / "S1_imbalance_01")
    (tmp_path / "raw" / "not_a_run").mkdir()
    assert [r.name for r in run_folders([tmp_path / "raw"])] == ["S1_imbalance_01", "S1_normal_free_01"]
    assert run_folders([tmp_path / "raw" / "S1_imbalance_01"])[0].name == "S1_imbalance_01"


def test_uploads_status_in_cloud_shape_and_windows_as_recorded(tmp_path):
    _run(tmp_path / "S1_normal_free_01")
    sender = FakeSender()
    assert main([str(tmp_path)], sender=sender) == 0
    status = [p for e, p in sender.sent if e == STATUS_ENDPOINT]
    windows = [p for e, p in sender.sent if e == WINDOWS_ENDPOINT]
    assert len(status) == 1 and len(windows) == 1
    assert status[0]["packet_id"] == "p" * 32  # the recorded ID: re-uploading is deduplicated
    assert status[0]["sensors"][0]["voltage_mv"] == 1582
    assert status[0]["sensors"][1]["z_g"] == -0.86
    assert status[0]["received_at_utc"] == "2026-11-08T23:00:00+00:00"
    assert windows[0]["packet_id"] == "w" * 32


def test_old_recordings_without_ids_get_stable_ones_and_machine_override(tmp_path):
    _run(tmp_path / "S1_off_01", with_ids=False)
    a = status_packets(tmp_path / "S1_off_01", "MG-002")
    b = status_packets(tmp_path / "S1_off_01", "MG-002")
    assert a[0]["packet_id"] == b[0]["packet_id"] and a[0]["machine_id"] == "MG-002"


def test_dry_run_sends_nothing(tmp_path, capsys):
    _run(tmp_path / "S1_normal_free_01")
    assert main([str(tmp_path), "--dry-run"]) == 0
    assert "1 status readings, 1 vibration windows" in capsys.readouterr().out
