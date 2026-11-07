"""Settings for the drill with the MPU-6050 accelerometer (1000 samples per second)."""

from processing import FFTConfig

FS = 1000.0          # MPU-6050 accelerometer output rate
WINDOW = 1024        # samples per window (about 1 s)
STEP = 512           # 50% overlap between windows

# The MPU-6050 low-pass filter (DLPF widest setting) leaves about 260 Hz usable.
DRILL_FFT = FFTConfig(
    sample_rate_hz=FS,
    window_size=WINDOW,
    welch_nperseg=256,
    max_freq_hz=260.0,
    bands_hz=((2.0, 40.0), (40.0, 120.0), (120.0, 260.0)),
)

LABELS = ("off", "normal_free", "normal_load", "imbalance", "bent_bit", "loose_bit", "overheat")
NORMAL_LABELS = ("normal_free", "normal_load")
BASELINE_LABEL = "normal_free"   # what the device records for 60 s when it is clipped on
FAULT_LABELS = tuple(l for l in LABELS if l not in NORMAL_LABELS and l != "off")

CSV_COLUMNS = ("t_us", "ax", "ay", "az", "temp_c")
TEMP_SLOPE_SECONDS = 10.0        # temperature trend is fitted over the last 10 s
