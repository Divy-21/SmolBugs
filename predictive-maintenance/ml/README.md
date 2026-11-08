# MachineGuard ML

Turns drill vibration (and, once the thermistor is verified, temperature) into "running normally", "machine off", or a named fault, and decides when to shut the drill off.

```
ESP32 + LSM303D  --Bluetooth-->  ml.record (ble_receiver --out)  ->  data/raw/S1_normal_free_01/  ->  features  ->  train.py  ->  models/machineguard.joblib
                                                                                                                                  |
                                                                live: detector.calibrate(normal windows)  ->  detector.predict(each window)
```

## Files

| File | What it does |
|---|---|
| `settings.py` | LSM303D settings: 1600 Hz, 1024-sample analysis windows, bands up to 360 Hz, labels, shutoff k-of-n |
| `record.py` | Records one labelled run over Bluetooth into `data/raw/S1_imbalance_03/` (runs the normal receiver with `--out`) |
| `recordings.py` | Loads run folders, cuts each Bluetooth window into analysis windows, builds the feature table |
| `features.py` | Per-window features (FFT via `processing.analyze_axis`, plus RMS, kurtosis, crest, skew, optional temperature) and baseline-relative features |
| `train.py` | Trains the isolation forest and the fault classifier, tests on a held-out session |
| `detector.py` | The live detector: learn normal, then check every window |
| `live_check.py` | Replays recorded runs through the detector the way the device would |

## Labels

`off`, `normal_free`, `normal_load`, `imbalance`, `bent_bit`, `loose_bit`, `overheat`

## Recording real data

First check the board already sends vibration (no flashing needed if it does):

```powershell
python -m ingest.ble_receiver --duration 60
```

`Vib window ...` lines mean it works. "Vibration characteristic not available" or
"older firmware" means the board needs the machine1 firmware before you can record.

Record one run per command. The run number is picked automatically:

```powershell
python -m ml.record --session 1 --label normal_free --seconds 300
python -m ml.record --session 1 --label imbalance --seconds 300
```

Each run is a folder written by the receiver: `windows/*.npz` (one Bluetooth window
of 4096 samples each), `vibration.jsonl` and `temperature.jsonl`. The board sends a
window every 10 s, so 300 s gives about 30 windows (210 analysis windows). The
recorder warns if windows were lost or none arrived.

A session is one mounting of the sensor. Record `normal_free` first in every session
(normal is learned from it), use at least 2 sessions (3 or more is better), and
re-clip the sensor between sessions.

Then train:

```powershell
python -m ml.train --raw data/raw --out models
python -m ml.live_check --raw data/raw --session 3
```

## Why analysis windows stay inside one Bluetooth window

Each Bluetooth window is 2.56 s of continuous samples, followed by a ~7.4 s gap.
An analysis window that crossed the gap would contain a jump that looks like an
impact (high kurtosis and crest factor, so a healthy run would look like
`loose_bit`). So each 4096-sample window is cut into 7 overlapping 1024-sample
analysis windows on its own.

## Temperature

Off by default. The thermistor values are unverified and it reads high, so training
on it would teach the model bad readings. Once it is checked against a thermometer,
train with `python -m ml.train --use-temp`. A model trained with temperature needs
`temp_c` and `temp_slope_c_per_min` in `calibrate()` and `predict()`; one trained
without it ignores them. `overheat` runs need `--use-temp` to be learnable.

## Why the features are "relative"

Every session is compared to its own `normal_free` runs, the same way the device learns normal when it is clipped on. A 1x peak three times bigger than normal means imbalance whether the sensor sits a bit closer or further from the chuck. Kurtosis, crest factor and the 2x/1x ratio have no units, so they carry over as they are.

## How the detector decides

Each window gets a status:


1. Main axis much quieter than normal, or the classifier says `off`: **Machine off**.
2. Classifier says normal and the isolation forest agrees: **Running normally**.
3. Classifier names a fault with at least 60% confidence, and either the isolation forest or the FFT rules agree: **Fault**.
4. Anything else: **Unusual vibration, type unknown**. It warns but does not shut off.

`shutoff` is only true when at least 4 of the last 7 windows were faults
(`SHUTOFF_K` / `SHUTOFF_N` in `settings.py`), so one bump never cuts the power.
Call `reset_shutoff()` after the machine is restarted.

## Testing honestly

Sessions are separate sensor mountings. `train.py` always tests on a session it never trained on, never on a random split of windows, because windows from the same run look almost identical and would inflate the score.

## Recommendations

Every result from `detector.predict()` includes a `recommendation`, for example "Tighten the chuck and check that the bit is seated all the way in." for a loose bit.

## Slow wear warning (bearings)

`wear.py` has `WearTracker`. Faults like imbalance show up in one window, but wear creeps in over days, so it judges each hour of use instead:

1. Learn normal from the first hours of use (isolation forest).
2. Each later hour: what share of windows look unusual, smoothed over 6 hours.
3. Over 50% for 3 hours in a row: "Bearing wear likely: schedule service or replace the bearing soon."
4. Temperature 15 °C above its normal rise, or vibration 3 times normal: "Critical: stop the machine now."

It is checked on the real Ball Bearing Run-to-Failure dataset (Kaggle, Jung et al. 2023), cut down to 1600 Hz so it matches what our LSM303D can see:

```powershell
python -m ml.bearing_validation extract --zip "C:/Users/rimsh/Downloads/archive.zip"
python -m ml.bearing_validation analyze
```

Result at the old 1000 Hz rate: learned normal from hours 0 to 24, sustained wear warning at hour 49, critical at hour 121, failure at hour 128. Re-run `extract` and `analyze` to get the numbers at 1600 Hz (extract redoes only the hours saved at the old rate). The chart is saved to `reports/bearing_wear_warning.png`.

What drove the early warning was the bearing running hotter than normal, not vibration. At 1000 Hz the vibration only clearly changed in the last few hours, which is why the thermistor matters.
