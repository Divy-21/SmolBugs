"""MachineGuard dashboard server.

    cd dashboard/server
    python -m pip install fastapi "uvicorn[standard]"
    uvicorn app:app --port 8000

Then open http://localhost:5173 (dev web app) or http://localhost:8000 (built web app).
On first start it makes fake drill recordings and trains a model on them if none exist,
so it runs before the IMU is ready. It starts in Demo mode.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
PM = ROOT / "predictive-maintenance"
sys.path.insert(0, str(PM))

from engine import Engine, ReplaySource, SerialSource  # noqa: E402

FAKE_RAW = PM / "data" / "fake" / "raw"
REAL_RAW = PM / "data" / "raw"
REAL_MODEL = PM / "models" / "machineguard.joblib"
FAKE_MODEL = PM / "models" / "fake" / "machineguard.joblib"
BEARING_CHART = PM / "reports" / "bearing_wear_warning.png"
WEB_DIST = ROOT / "dashboard" / "web" / "dist"

# The story the demo plays: learn normal, then each fault with normal running in between
DEMO = ["normal_free_01", "normal_free_02", "imbalance_01", "normal_free_03", "bent_bit_01",
        "normal_load_01", "loose_bit_01", "normal_load_02", "overheat_01", "off_01"]
DEMO_SESSION = 3
LEARN_SECONDS_REPLAY = 15.0
LEARN_SECONDS_LIVE = 60.0


def ensure_model() -> Path:
    if REAL_MODEL.exists():
        return REAL_MODEL
    if not FAKE_MODEL.exists():
        print("No model yet: making fake recordings and training on them (about a minute)...")
        if not any(FAKE_RAW.glob("*.csv")):
            subprocess.run([sys.executable, "-m", "ml.fake_recordings", "--out", str(FAKE_RAW)], cwd=PM, check=True)
        subprocess.run([sys.executable, "-m", "ml.train", "--raw", str(FAKE_RAW), "--out", str(FAKE_MODEL.parent)],
                       cwd=PM, check=True)
    return FAKE_MODEL


def recordings() -> list[Path]:
    return sorted(REAL_RAW.glob("S*.csv")) + sorted(FAKE_RAW.glob("S*.csv"))


def demo_source() -> ReplaySource:
    paths = [FAKE_RAW / f"S{DEMO_SESSION}_{name}.csv" for name in DEMO]
    return ReplaySource([p for p in paths if p.exists()])


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def send(self, msg: dict) -> None:
        text = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.clients.discard(ws)


hub = Hub()
engine: Engine | None = None


async def pump() -> None:
    while True:
        try:
            for msg in engine.step():
                await hub.send(msg)
        except Exception as exc:  # keep the dashboard alive if one window fails
            print(f"engine error: {exc}")
        await asyncio.sleep(0.05)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global engine
    engine = Engine(ensure_model())
    engine.set_source(demo_source(), LEARN_SECONDS_REPLAY)
    task = asyncio.create_task(pump())
    yield
    task.cancel()


app = FastAPI(title="MachineGuard", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SourceRequest(BaseModel):
    mode: str                     # "demo", "replay" or "serial"
    file: str | None = None
    port: str | None = None


class CalibrateRequest(BaseModel):
    seconds: float | None = None


@app.get("/api/recordings")
def list_recordings():
    return {"demo": DEMO, "files": [p.name for p in recordings()]}


@app.get("/api/ports")
def list_ports():
    try:
        from serial.tools import list_ports as lp
        return {"ports": [{"device": p.device, "description": p.description} for p in lp.comports()]}
    except Exception:
        return {"ports": []}


@app.post("/api/source")
async def set_source(req: SourceRequest):
    try:
        if req.mode == "demo":
            engine.set_source(demo_source(), LEARN_SECONDS_REPLAY)
        elif req.mode == "replay":
            match = [p for p in recordings() if p.name == req.file]
            if not match:
                raise HTTPException(404, f"recording {req.file} not found")
            # learn from a normal run of the same session first, then play the chosen file
            session = match[0].name.split("_")[0]
            normal = sorted(match[0].parent.glob(f"{session}_normal_free_*.csv"))[:1]
            engine.set_source(ReplaySource(normal + match), LEARN_SECONDS_REPLAY)
        elif req.mode == "serial":
            if not req.port:
                raise HTTPException(400, "port is required for serial mode")
            engine.set_source(SerialSource(req.port), LEARN_SECONDS_LIVE)
        else:
            raise HTTPException(400, "mode must be demo, replay or serial")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    await hub.send(engine.events[0])
    return {"ok": True}


@app.post("/api/calibrate")
async def calibrate(req: CalibrateRequest):
    default = LEARN_SECONDS_LIVE if engine.source and engine.source.name == "serial" else LEARN_SECONDS_REPLAY
    engine.calibrate(req.seconds or default)
    await hub.send(engine.events[0])
    return {"ok": True}


@app.post("/api/reset")
async def reset():
    engine.reset()
    if engine.events:
        await hub.send(engine.events[0])
    return {"ok": True}


@app.get("/api/state")
def state():
    return {"last": engine.last_message, "events": list(engine.events)}


@app.get("/api/bearing-chart")
def bearing_chart():
    if not BEARING_CHART.exists():
        raise HTTPException(404, "run python -m ml.bearing_validation analyze first")
    return FileResponse(BEARING_CHART)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    hub.clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "history", "events": list(engine.events)}))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.clients.discard(websocket)


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
