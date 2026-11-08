"""Stable identities for readings, and a background uploader to the SmolBugs cloud.

Identity
--------
Every status packet and analysed window gets three fields:

  boot_id    the firmware's boot_id if it sends one; otherwise made by the gateway when it
             connects to the board, and again whenever that machine's device clock goes
             backwards (the board rebooted).
  seq        the firmware's seq / sequence if present (windows use window_id); otherwise a
             gateway counter per (machine_id, boot_id), starting at 0.
  packet_id  sha256("machine_id|boot_id|kind|seq")[:32], kind = "status" or "window".

Sensor values are never hashed: two different readings can have identical values, and a
retry must produce the same ID.

Device clock used for reset detection: timestamp_ms for JSON status packets; for the CSV
status messages (which carry no timestamp) the firmware sequence number; first_sample_ms for
windows. Each kind is tracked separately, because a window's first_sample_ms is older than
the status packets that arrive while it is being sent.

Upload
------
CloudSender.submit() only appends to an in-memory backlog, so Bluetooth handlers never wait
on the network. A worker thread POSTs up to 50 packets per request at least once a second,
retries failures with backoff using the same packets (and so the same packet_ids), caps the
backlog by dropping the oldest, drops packets the server rejects with 400, and reports 401.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

STATUS_ENDPOINT = "/api/v1/status"
WINDOWS_ENDPOINT = "/api/v1/windows"

# The team's AWS deployment. CLOUD_URL (environment or settings file) overrides it.
DEFAULT_CLOUD_URL = "https://sm-db6bb08ece0d477c9e820f118f7e670c.ecs.ca-central-1.on.aws"
# KEY=VALUE lines (CLOUD_URL, DEVICE_KEY) saved once per laptop; .gitignore keeps it out of git.
# SMOLBUGS_ENV_FILE points somewhere else (tests use it).
SETTINGS_FILE = Path(__file__).resolve().parents[2] / "smolbugs.env"


# ---------------------------------------------------------------- identity

def make_packet_id(machine_id: str, boot_id: str, kind: str, seq: int) -> str:
    return hashlib.sha256(f"{machine_id}|{boot_id}|{kind}|{seq}".encode("utf-8")).hexdigest()[:32]


def new_boot_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class _MachineState:
    boot_id: str
    counter: int = 0
    last_clock: dict[str, int] = field(default_factory=dict)  # per kind
    last_seq: dict[str, int] = field(default_factory=dict)    # per kind, for wraparound
    wraps: dict[str, int] = field(default_factory=dict)


class IdentityTracker:
    """Assigns boot_id / seq / packet_id. Not thread-safe; call from the Bluetooth handler."""

    def __init__(self, boot_id_factory: Callable[[], str] = new_boot_id):
        self._new_boot_id = boot_id_factory
        self._machines: dict[str, _MachineState] = {}
        self.resets_detected = 0

    def on_connect(self) -> None:
        """A new connection to the board: the gateway makes fresh boot_ids."""
        self._machines.clear()

    def _state(self, machine_id: str) -> _MachineState:
        if machine_id not in self._machines:
            self._machines[machine_id] = _MachineState(self._new_boot_id())
        return self._machines[machine_id]

    def _check_clock(self, machine_id: str, kind: str, clock: Any) -> _MachineState:
        state = self._state(machine_id)
        if isinstance(clock, (int, float)) and not isinstance(clock, bool):
            last = state.last_clock.get(kind)
            if last is not None and clock < last:
                # The device clock went backwards: the board rebooted.
                self.resets_detected += 1
                state = self._machines[machine_id] = _MachineState(self._new_boot_id())
            state.last_clock[kind] = clock
        return state

    def identify(self, kind: str, machine_id: str, clock: Any = None,
                 firmware_boot_id: Any = None, firmware_seq: Any = None,
                 seq_wraps_at: int | None = None) -> dict[str, Any]:
        """Return {"boot_id", "seq", "packet_id"} for one reading.

        seq_wraps_at: the firmware counter's range (65536 for the u16 window_id). When it
        restarts from a smaller value without a reboot, seq keeps counting up instead of
        repeating earlier IDs.
        """
        if kind not in ("status", "window"):
            raise ValueError(f"kind must be 'status' or 'window', got {kind!r}")
        machine_id = str(machine_id)
        state = self._check_clock(machine_id, kind, clock)
        boot_id = str(firmware_boot_id) if firmware_boot_id not in (None, "") else state.boot_id
        if firmware_seq is not None:
            seq = int(firmware_seq)
            if seq_wraps_at:
                last = state.last_seq.get(kind)
                if last is not None and seq < last:
                    state.wraps[kind] = state.wraps.get(kind, 0) + 1
                state.last_seq[kind] = seq
                seq += state.wraps.get(kind, 0) * seq_wraps_at
        else:
            seq = state.counter
            state.counter += 1
        return {"boot_id": boot_id, "seq": seq, "packet_id": make_packet_id(machine_id, boot_id, kind, seq)}


# ---------------------------------------------------------------- JSON safety

def json_safe(value: Any) -> Any:
    """Copy of value that json.dumps(allow_nan=False) accepts: NaN/inf -> None, numpy -> Python."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):  # numpy scalars/arrays
        return json_safe(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# ---------------------------------------------------------------- HTTP

class PostResult:
    def __init__(self, status: int | None, body: str = "", error: str | None = None):
        self.status, self.body, self.error = status, body, error


def urllib_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> PostResult:
    """POST with the standard library. Network problems come back as status=None."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return PostResult(resp.status, resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as err:
        return PostResult(err.code, err.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return PostResult(None, error=str(getattr(err, "reason", err)))


def _details(body: str) -> str:
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and "details" in parsed:
            return json.dumps(parsed["details"])
    except ValueError:
        pass
    return body[:300]


@dataclass
class SenderStats:
    sent: int = 0
    requests: int = 0
    retries: int = 0
    dropped_backlog: int = 0
    dropped_rejected: int = 0
    auth_failures: int = 0


class CloudSender:
    """Background uploader. submit() never blocks on the network and never raises."""

    def __init__(
        self,
        base_url: str,
        device_key: str,
        post: Callable[[str, bytes, dict[str, str], float], PostResult] = urllib_post,
        log: Callable[[str], None] = print,
        batch_size: int = 50,
        flush_interval_s: float = 1.0,
        max_backlog: int = 2000,  # ~10 min: status every 0.5 s (1200) + windows every 10 s (60), with headroom
        timeout_s: float = 10.0,
        max_backoff_s: float = 60.0,
        sleep: Callable[[float], None] | None = None,
    ):
        if not base_url:
            raise ValueError("base_url is required")
        if not device_key:
            raise ValueError("device_key is required")
        self.base_url = base_url.rstrip("/")
        self._key = device_key
        self._post = post
        self.log = log
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.max_backlog = max_backlog
        self.timeout_s = timeout_s
        self.max_backoff_s = max_backoff_s
        self.stats = SenderStats()
        self._backlog: deque[tuple[str, dict[str, Any]]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._backoff_s = 0.0
        self._thread: threading.Thread | None = None
        self._sleep = sleep or (lambda s: self._stop.wait(s))

    # -- producer side (Bluetooth handlers) --------------------------------
    def submit(self, endpoint: str, packet: dict[str, Any]) -> None:
        try:
            item = (endpoint, json_safe(packet))
            with self._lock:
                self._backlog.append(item)
                overflow = len(self._backlog) - self.max_backlog
                for _ in range(max(0, overflow)):
                    self._backlog.popleft()
                before = self.stats.dropped_backlog
                if overflow > 0:
                    self.stats.dropped_backlog += overflow
                after = self.stats.dropped_backlog
                full = len(self._backlog) >= self.batch_size
            if after > before and (before == 0 or after // 100 > before // 100):
                self.log(f"Cloud backlog full: dropped {after} oldest packet(s) so far")
            if full:
                self._wake.set()
        except Exception as err:  # must never break the Bluetooth handler
            self.log(f"Cloud submit failed: {err}")

    @property
    def backlog(self) -> int:
        with self._lock:
            return len(self._backlog)

    # -- worker ---------------------------------------------------------------
    def start(self) -> "CloudSender":
        self._thread = threading.Thread(target=self._run, name="smolbugs-cloud", daemon=True)
        self._thread.start()
        return self

    def close(self, flush_timeout_s: float = 5.0) -> None:
        """Try to send what's left (up to flush_timeout_s), then stop the worker."""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=self.timeout_s + 2.0)
        deadline = time.monotonic() + flush_timeout_s
        while self.backlog and time.monotonic() < deadline:
            if not self.flush_once():
                break
        if self.backlog:
            self.log(f"Cloud: {self.backlog} packet(s) not sent before exit")

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.flush_interval_s)
            self._wake.clear()
            if self._stop.is_set():
                break
            # Send everything waiting (a backlog after an outage catches up quickly).
            while self.backlog and not self._stop.is_set():
                if not self.flush_once():
                    self._sleep(self._backoff_s)
                    break

    def _take_batch(self) -> tuple[str, list[dict[str, Any]]]:
        """Up to batch_size of the oldest packets for the endpoint of the oldest packet.

        Packets for the other endpoint are skipped over, not blocking, so interleaved
        status and window packets still go up in full batches. Not removed yet.
        """
        with self._lock:
            if not self._backlog:
                return "", []
            endpoint = self._backlog[0][0]
            batch = []
            for ep, pkt in self._backlog:
                if ep == endpoint:
                    batch.append(pkt)
                    if len(batch) >= self.batch_size:
                        break
            return endpoint, batch

    def _remove(self, packets: list[dict[str, Any]]) -> None:
        ids = {id(p) for p in packets}
        with self._lock:
            self._backlog = deque(item for item in self._backlog if id(item[1]) not in ids)

    def _send(self, endpoint: str, packets: list[dict[str, Any]]) -> PostResult:
        body = json.dumps(packets, allow_nan=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "x-device-key": self._key}
        self.stats.requests += 1
        try:
            return self._post(self.base_url + endpoint, body, headers, self.timeout_s)
        except Exception as err:  # a misbehaving transport is treated as a network failure
            return PostResult(None, error=str(err))

    def flush_once(self) -> bool:
        """Send one batch. Returns True if progress was made (sent or dropped)."""
        endpoint, batch = self._take_batch()
        if not batch:
            return True
        result = self._send(endpoint, batch)

        if result.status is not None and 200 <= result.status < 300:
            self._remove(batch)
            self.stats.sent += len(batch)
            self._backoff_s = 0.0
            return True

        if result.status == 400:
            if len(batch) == 1:
                self._remove(batch)
                self.stats.dropped_rejected += 1
                self.log(f"Cloud rejected packet {batch[0].get('packet_id')} (400): {_details(result.body)}")
                self._backoff_s = 0.0
                return True
            # Find the bad packet(s): resend one at a time so good ones still get through.
            for pkt in batch:
                if not self.flush_single(endpoint, pkt):
                    return False
            return True

        if result.status == 401:
            self.stats.auth_failures += 1
            self.log("Cloud rejected DEVICE_KEY (401 Unauthorized). Check the DEVICE_KEY environment "
                     "variable; packets are kept and retried.")
        else:
            reason = f"HTTP {result.status}" if result.status else (result.error or "network error")
            self.log(f"Cloud send failed ({reason}); {self.backlog} packet(s) waiting, will retry")
        self.stats.retries += 1
        self._backoff_s = min(self.max_backoff_s, max(1.0, self._backoff_s * 2))
        return False

    def flush_single(self, endpoint: str, pkt: dict[str, Any]) -> bool:
        result = self._send(endpoint, [pkt])
        if result.status is not None and 200 <= result.status < 300:
            self._remove([pkt])
            self.stats.sent += 1
            return True
        if result.status == 400:
            self._remove([pkt])
            self.stats.dropped_rejected += 1
            self.log(f"Cloud rejected packet {pkt.get('packet_id')} (400): {_details(result.body)}")
            return True
        self.stats.retries += 1
        self._backoff_s = min(self.max_backoff_s, max(1.0, self._backoff_s * 2))
        return False

    def summary(self) -> str:
        s = self.stats
        return (f"cloud: sent {s.sent} | waiting {self.backlog} | retries {s.retries} | "
                f"dropped (backlog full) {s.dropped_backlog} | rejected (400) {s.dropped_rejected}")


def settings_file() -> Path:
    return Path(os.environ.get("SMOLBUGS_ENV_FILE") or SETTINGS_FILE)


def read_settings(path: Path | None = None) -> dict[str, str]:
    """KEY=VALUE pairs from the settings file; {} if it doesn't exist."""
    path = path or settings_file()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    settings = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        settings[name.strip()] = value.strip().strip("\"'")
    return settings


def save_setting(name: str, value: str, path: Path | None = None) -> Path:
    """Set one KEY=VALUE in the settings file, keeping its other lines."""
    path = path or settings_file()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = ["# SmolBugs receiver settings for this laptop. Never commit or share this file."]
    lines = [l for l in lines if l.partition("=")[0].strip() != name] + [f"{name}={value}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def cloud_settings() -> tuple[str, str]:
    """(url, key): environment variables first, then the settings file; the URL defaults to AWS."""
    saved = read_settings()
    url = (os.environ.get("CLOUD_URL") or saved.get("CLOUD_URL") or DEFAULT_CLOUD_URL).strip()
    key = (os.environ.get("DEVICE_KEY") or saved.get("DEVICE_KEY") or "").strip()
    return url, key


def sender_from_env(log: Callable[[str], None] = print, **kwargs) -> CloudSender:
    """Build a CloudSender from cloud_settings(). Raises ValueError if there is no device key."""
    url, key = cloud_settings()
    if not key:
        raise ValueError(f"no device key: set DEVICE_KEY, or put DEVICE_KEY=... in {settings_file()} "
                         "(the receiver.py launcher asks for it once and saves it there)")
    return CloudSender(url, key, log=log, **kwargs)
