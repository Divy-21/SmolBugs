# MachineGuard ML

Turns drill vibration and temperature into "running normally", "machine off", or a named fault, and decides when to shut the drill off.

```
ESP32 + MPU-6050  ->  record.py  ->  data/raw/*.csv  ->  features (team FFT code)  ->  train.py  ->  models/machineguard.joblib
                                                                                                        |
                                              live: detector.calibrate(60 s of normal)  ->  detector.predict(each window)
```

## Files

| File | What it does |
|---|---|
| `settings.py` | Drill settings: 1000 Hz, 1024-sample windows, 50% overlap, bands up to 260 Hz, labels |
| `record.py` | Records one run from the ESP32 over USB serial into `data/raw/S1_imbalance_03.csv` |
| `recordings.py` | Loads runs, checks for dropped samples, cuts 1 s windows, builds the feature table |
| `features.py` | Per-window features (FFT via `processing.analyze_axis`, plus RMS, kurtosis, crest, skew, temperature trend) and baseline-relative features |
| `fake_recordings.py` | Fake runs in the exact recorder format, so everything can be tested before the IMU works |
| `train.py` | Trains the isolation forest and the fault classifier, tests on a held-out session |
| `detector.py` | The live detector: learn normal, then check every window |
| `live_check.py` | Replays recorded runs through the detector the way the device would |

## Labels

`off`, `normal_free`, `normal_load`, `imbalance`, `bent_bit`, `loose_bit`, `overheat`

## Try it now with fake data

```powershell
python -m pip install numpy scipy pandas scikit-learn joblib pyserial
python -m ml.fake_recordings --out data/fake/raw
python -m ml.train --raw data/fake/raw --out models/fake
python -m ml.live_check --raw data/fake/raw --session 3 --models models/fake/machineguard.joblib
```

## Recording real data

The ESP32 must print one line per sample at 921600 baud, 1000 lines per second:

```
t_us,ax,ay,az,temp_c
1234567,0.0123,-0.0456,1.0021,24.8
```

`t_us` comes from `micros()`, acceleration is in g, temperature is the last thermistor reading in °C.

```powershell
python -m ml.record --list-ports
python -m ml.record --port COM5 --session 1 --label normal_free --seconds 60
```

The run number is picked automatically. The script warns if samples were dropped or the rate is wrong.

Then train on the real data:

```powershell
python -m ml.train --raw data/raw --out models
```

## Why the features are "relative"

Every session is compared to its own `normal_free` runs, the same way the device learns normal when it is clipped on. A 1x peak three times bigger than normal means imbalance whether the sensor sits a bit closer or further from the chuck. Kurtosis, crest factor and the 2x/1x ratio have no units, so they carry over as they are.

## How the detector decides

1. Main axis much quieter than normal, or the classifier says `off`: **Machine off**.
2. Classifier says normal and the isolation forest agrees: **Running normally**.
3. Classifier names a fault with at least 60% confidence, and either the isolation forest or the FFT rules agree: **Fault, shut off**.
4. Anything else: **Unusual vibration, type unknown**. It warns but does not shut off.

## Testing honestly

Sessions are separate sensor mountings. `train.py` always tests on a session it never trained on, never on a random split of windows, because windows from the same run look almost identical and would inflate the score.
