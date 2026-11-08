"""Settings for the drill with the LSM303D accelerometer (1600 samples per second).

The board sends one 4096-sample window every 10 s over Bluetooth. Each of those
is cut into shorter analysis windows that never cross from one Bluetooth window
into the next (there is a ~7.4 s gap between them).
"""

from processing import FFTConfig

FS = 1600.0          # LSM303D output data rate
FS_TOLERANCE = 0.05  # warn if a recording's sample rate is more than 5% off
WINDOW = 1024        # samples per analysis window (0.64 s, 1.56 Hz resolution)
STEP = 512           # 50% overlap, only inside one Bluetooth window: 7 per window

# The LSM303D anti-alias filter is set to 362 Hz, so nothing above ~360 Hz is usable.
DRILL_FFT = FFTConfig(
    sample_rate_hz=FS,
    window_size=WINDOW,
    welch_nperseg=256,
    max_freq_hz=360.0,
    bands_hz=((2.0, 40.0), (40.0, 120.0), (120.0, 360.0)),
)

LABELS = ("off", "normal_free", "normal_load", "imbalance", "bent_bit", "loose_bit", "overheat")
NORMAL_LABELS = ("normal_free", "normal_load")
BASELINE_LABEL = "normal_free"   # what the device records when it is clipped on
FAULT_LABELS = tuple(l for l in LABELS if l not in NORMAL_LABELS and l != "off")

# Temperature arrives every 0.5 s in the status messages; the trend is fitted over
# the last minute of readings before each Bluetooth window.
TEMP_SLOPE_SECONDS = 60.0
TEMP_SLOPE_MIN_READINGS = 5

# Shutoff only when most recent windows agree it is a fault (k of the last n).
# 7 analysis windows come from each 2.56 s Bluetooth window, so 4 of 7 means
# "most of one Bluetooth window", not one bump.
SHUTOFF_K = 4
SHUTOFF_N = 7
