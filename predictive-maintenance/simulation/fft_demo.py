"""SmolBugs FFT demo on synthetic data: healthy vs faulty machine, plotted with Plotly.

Run from the project root: python -m simulation.fft_demo   ->  writes smolbugs_fft_demo.html
"""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from processing import FFTConfig, amplitude_spectrum, band_power, dominant_frequency, harmonic_amplitudes, rpm_to_hz, welch_psd
from simulation.synthetic import synth_signal

cfg = FFTConfig()
FS, N, RPM = cfg.sample_rate_hz, cfg.window_size, 3000.0
f1 = rpm_to_hz(RPM)

signals = {
    "healthy": synth_signal(FS, N, [(f1, 0.05), (2 * f1, 0.01)], noise_std=0.01, dc=1.0, seed=0),
    "faulty": synth_signal(FS, N, [(f1, 0.25), (2 * f1, 0.12)], noise_std=0.01, dc=1.0, seed=7,
                           impulse_rate_hz=87.0, impulse_amp=0.8),
}
colors = {"healthy": "#2a9d8f", "faulty": "#e76f51"}

fig = make_subplots(rows=3, cols=1, vertical_spacing=0.09,
                    subplot_titles=("Time domain (first 0.2 s)", "FFT amplitude spectrum", "Welch PSD"))
t = np.arange(N) / FS
show = t < 0.2
for label, x in signals.items():
    c = colors[label]
    spec = amplitude_spectrum(x, FS, cfg.detrend_type)
    f, psd = welch_psd(x, FS, cfg.welch_nperseg, cfg.welch_overlap, cfg.detrend_type)
    fig.add_trace(go.Scatter(x=t[show], y=x[show], name=label, legendgroup=label, line=dict(color=c, width=1)), 1, 1)
    fig.add_trace(go.Scatter(x=spec.freqs_hz, y=spec.amplitude, legendgroup=label, showlegend=False, line=dict(color=c, width=1)), 2, 1)
    fig.add_trace(go.Scatter(x=f, y=psd, legendgroup=label, showlegend=False, line=dict(color=c, width=1)), 3, 1)

    fd, ad = dominant_frequency(spec, cfg.min_freq_hz, cfg.max_freq_hz)
    harm = harmonic_amplitudes(spec, f1, n_harmonics=3)
    print(f"\n--- {label} ---")
    print(f"  dominant: {fd:.3f} Hz @ {ad:.4f} g")
    print("  harmonics: " + ", ".join(f"{int(h['order'])}x={h['amplitude']:.3f} g" for h in harm))
    for lo, hi in cfg.bands_hz:
        print(f"  band {lo:>4g}-{hi:<5g} Hz: {band_power(f, psd, lo, hi):.3e} g²")

for order in (1, 2, 3):
    fig.add_vline(x=order * f1, line_dash="dot", line_color="gray", row=2, col=1,
                  annotation_text=f"{order}x", annotation_position="top")
fig.update_xaxes(title_text="time (s)", row=1, col=1)
fig.update_xaxes(title_text="frequency (Hz)", range=[0, cfg.max_freq_hz], row=2, col=1)
fig.update_xaxes(title_text="frequency (Hz)", range=[0, cfg.max_freq_hz], row=3, col=1)
fig.update_yaxes(title_text="g", row=1, col=1)
fig.update_yaxes(title_text="g (peak)", row=2, col=1)
fig.update_yaxes(title_text="g²/Hz", type="log", row=3, col=1)
fig.update_layout(height=1000, title=f"SmolBugs FFT demo: {RPM:.0f} RPM ({f1:.0f} Hz running speed)")
fig.write_html("smolbugs_fft_demo.html", include_plotlyjs=True)
print("\nwrote smolbugs_fft_demo.html")
