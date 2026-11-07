"""Builds an interactive FFT report (HTML) from a folder saved by the BLE receiver.

    python -m ingest.ble_receiver --rpm 3000 --out data/session1     # record
    python -m ingest.fft_report data/session1 --rpm 3000             # build report
    start data/session1/fft_report.html                              # open it

The receiver can also keep the report updated live with --live-html.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from processing import FFTConfig, amplitude_spectrum, rpm_to_hz, welch_psd

AXIS_COLORS = {"x": "#e76f51", "y": "#2a9d8f", "z": "#4361ee"}
REPORT_NAME = "fft_report.html"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written last line while the receiver is running
    return rows


def _time(row: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(row["received_at_utc"])
    except (KeyError, ValueError):
        return None


def build_report(session_dir: Path, rpm: float | None = None, window_id: int | None = None,
                 cfg: FFTConfig = FFTConfig()) -> go.Figure:
    """Figure with the chosen (default: latest) window's spectrum plus session trends."""
    session_dir = Path(session_dir)
    vib = _read_jsonl(session_dir / "vibration.jsonl")
    temps = _read_jsonl(session_dir / "temperature.jsonl")

    chosen = None
    candidates = vib if window_id is None else [r for r in vib if r.get("window_id") == window_id]
    for row in reversed(candidates):
        path = session_dir / "windows" / f"{row['machine_id']}_w{row['window_id']:05d}.npz"
        if path.exists():
            chosen = (row, path)
            break
    if window_id is not None and chosen is None:
        raise FileNotFoundError(f"no saved raw data for window {window_id} in {session_dir}")

    fig = make_subplots(
        rows=5, cols=1, vertical_spacing=0.06,
        subplot_titles=(
            "Raw vibration, first 0.2 s (mean removed)",
            "FFT amplitude spectrum",
            "Welch power spectral density",
            "Vibration severity per window (velocity RMS)",
            "Temperature",
        ),
    )

    title = "SmolBugs FFT report"
    if chosen:
        row, path = chosen
        data = np.load(path)
        accel = data["accel_g"].astype(float)
        fs = float(data["sample_rate_hz"])
        t = np.arange(accel.shape[0]) / fs
        show = t < 0.2
        for i, ax in enumerate("xyz"):
            sig = accel[:, i]
            spec = amplitude_spectrum(sig, fs, cfg.detrend_type)
            f, psd = welch_psd(sig, fs, cfg.welch_nperseg, cfg.welch_overlap, cfg.detrend_type)
            style = dict(color=AXIS_COLORS[ax], width=1)
            fig.add_trace(go.Scatter(x=t[show], y=sig[show] - sig.mean(), name=f"{ax} axis",
                                     legendgroup=ax, line=style), 1, 1)
            fig.add_trace(go.Scatter(x=spec.freqs_hz, y=spec.amplitude, name=f"{ax} axis",
                                     legendgroup=ax, showlegend=False, line=style), 2, 1)
            fig.add_trace(go.Scatter(x=f, y=psd, name=f"{ax} axis", legendgroup=ax,
                                     showlegend=False, line=style), 3, 1)
        if rpm:
            f1 = rpm_to_hz(rpm)
            for order in (1, 2, 3):
                fig.add_vline(x=order * f1, line_dash="dot", line_color="gray", row=2, col=1,
                              annotation_text=f"{order}x", annotation_position="top")
        x = row["axes"]["x"]
        when = _time(row)
        title = (f"SmolBugs FFT: {row['machine_id']}, window {row['window_id']}"
                 f"{' at ' + when.astimezone().strftime('%Y-%m-%d %H:%M:%S') if when else ''}"
                 f"  |  x: {x['dominant_freq_hz']:.2f} Hz @ {x['dominant_amp_g']:.3f} g, "
                 f"{x['velocity_rms_mm_s']:.2f} mm/s")
        max_f = min(cfg.max_freq_hz, fs / 2)
        fig.update_xaxes(range=[0, max_f], row=2, col=1)
        fig.update_xaxes(range=[0, max_f], row=3, col=1)
    else:
        fig.add_annotation(text="No vibration windows saved yet", xref="x domain", yref="y domain",
                           x=0.5, y=0.5, showarrow=False, row=1, col=1)

    trend = [r for r in vib if _time(r)]
    for ax in "xyz":
        fig.add_trace(go.Scatter(x=[_time(r) for r in trend],
                                 y=[r["axes"][ax]["velocity_rms_mm_s"] for r in trend],
                                 mode="lines+markers", name=f"{ax} axis", legendgroup=ax, showlegend=False,
                                 line=dict(color=AXIS_COLORS[ax], width=1), marker=dict(size=4)), 4, 1)

    temp_rows = [r for r in temps if _time(r) and r.get("temperature_c") is not None]
    fig.add_trace(go.Scatter(x=[_time(r) for r in temp_rows], y=[r["temperature_c"] for r in temp_rows],
                             mode="lines", name="temperature", line=dict(color="#c1121f", width=1.5)), 5, 1)

    fig.update_xaxes(title_text="time (s)", row=1, col=1)
    fig.update_xaxes(title_text="frequency (Hz)", row=2, col=1)
    fig.update_xaxes(title_text="frequency (Hz)", row=3, col=1)
    fig.update_yaxes(title_text="g", row=1, col=1)
    fig.update_yaxes(title_text="g (peak)", row=2, col=1)
    fig.update_yaxes(title_text="g²/Hz", type="log", row=3, col=1)
    fig.update_yaxes(title_text="mm/s", row=4, col=1)
    fig.update_yaxes(title_text="°C (assumed NTC values)", row=5, col=1)
    fig.update_layout(height=1600, title=title, hovermode="x unified")
    return fig


def write_report(session_dir: Path, rpm: float | None = None, window_id: int | None = None,
                 refresh_s: int | None = None) -> Path:
    """Write fft_report.html into the session folder. refresh_s makes the browser reload it."""
    session_dir = Path(session_dir)
    out = session_dir / REPORT_NAME
    # 'directory' writes plotly.min.js once next to the report instead of 4 MB per update.
    html = build_report(session_dir, rpm, window_id).to_html(include_plotlyjs="directory", full_html=True)
    if refresh_s:
        html = html.replace("<head>", f'<head><meta http-equiv="refresh" content="{int(refresh_s)}">', 1)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(out)  # atomic swap so the browser never loads a half-written page
    js = session_dir / "plotly.min.js"
    if not js.exists():
        from plotly.offline import get_plotlyjs
        js.write_text(get_plotlyjs(), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build an FFT report from a saved receiver session")
    parser.add_argument("session_dir", type=Path, help="folder given to the receiver's --out")
    parser.add_argument("--rpm", type=float, help="mark 1x/2x/3x running-speed lines")
    parser.add_argument("--window", type=int, help="show this window instead of the latest")
    args = parser.parse_args(argv)
    if not args.session_dir.is_dir():
        parser.error(f"{args.session_dir} is not a folder")
    out = write_report(args.session_dir, args.rpm, args.window)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
