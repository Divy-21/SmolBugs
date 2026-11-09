# MachineGuard ML

Turns drill vibration (and, once the thermistor is verified, temperature) into "running normally", "machine off", "unusual vibration" or a named fault, and decides when to shut the drill off.

```
ESP32 + LSM303D  --Bluetooth-->  ml.record (ble_receiver --out)  ->  data/raw/S1_normal_free_01/  ->  features  ->  train.py  ->  models/machineguard.joblib
                                                                                                                                  |
                                                                live: detector.calibrate(normal windows)  ->  detector.predict(each window)
```

Trained only on real recordings from the drill. The generated data in `tests/ml_fakes.py` is for testing the code, never for training.

## Current model (session 1, November 2026)

| | |
|---|---|
| Data | Session 1 only, 10 runs: `normal_free` ×2, `normal_load` ×3, `loose_bit` ×3, `off` ×2 (1,120 analysis windows) |
| Not in the model | `imbalance` (the sensor wire came loose, so those runs saved no vibration), `bent_bit` (skipped), `overheat` (needs temperature) |
| Trained with | `python -m ml.train --raw data/raw --out models --holdout-run` |
| Classifier on held-out runs | 97.2% (leave-one-run-out: 83–96%) |
| Isolation forest on held-out runs | flags 0% of `normal_free`, 40% of `normal_load`, 16% of `loose_bit` windows as unusual |
| Running speed estimate | about 110 Hz (6,600 RPM): not yet checked against the drill's rating |

What that means in use (replayed with `ml.live_check`):

- `normal_free`, `normal_load`: **Running normally**, no shutoffs.
- `off`: **Machine off**.
- `loose_bit`: **Unusual vibration, type unknown: inspect the drill**. The classifier recognises the loose bit, but the isolation forest mostly does not find it unusual (drilling into material already shakes the drill as much), so the two checks disagree and it never counts as a fault. It warns but does not shut the drill off. This is kept on purpose for the demo.
- An imbalanced drill (not trained) should still look unusual to the isolation forest, but the classifier cannot name it.

All of these scores come from the same sensor mounting as the training data, so they are optimistic. Expect more false alarms after the sensor is unclipped and clipped back on, until it relearns normal.

## Files

| File | What it does |
|---|---|
| `settings.py` | LSM303D settings: 1600 Hz, 1024-sample analysis windows, bands up to 360 Hz, labels, shutoff k-of-n |
| `record.py` | Records one labelled run over Bluetooth into `data/raw/S1_imbalance_03/` (runs the normal receiver with `--out`) |
| `recordings.py` | Loads run folders, cuts each Bluetooth window into analysis windows, builds the feature table. Runs with no saved vibration are skipped with a warning |
| `features.py` | Per-window features (FFT via `processing.analyze_axis`, plus RMS, kurtosis, crest, skew, optional temperature) and baseline-relative features |
| `train.py` | Trains the isolation forest and the fault classifier; tests on a held-out session, or on held-out runs with `--holdout-run` |
| `detector.py` | The live detector: learn normal, then check every window |
| `live_check.py` | Replays recorded runs through the detector the way the device would |
| `wear.py`, `bearing_validation.py` | Slow bearing-wear warning, checked on a public bearing dataset (see the end) |

## Setup

From `E:\SmolBugs`, with the virtual environment active (the prompt starts with `(.venv)`):

```powershell
.venv\Scripts\Activate.ps1
cd predictive-maintenance
python -m pip install numpy scipy pandas scikit-learn joblib plotly matplotlib bleak pytest
```

A virtual environment only works on the computer that created it. If activating it gives `No Python at '...'`, delete `.venv` and create it again with `python -m venv .venv`.

## Labels

| Label | How to record it |
|---|---|
| `normal_free` | Drill running in the air, healthy bit properly tightened. **Always record first**: normal is learned from it |
| `off` | Sensor mounted, drill switched off |
| `normal_load` | Healthy bit, drilling continuously into material |
| `imbalance` | Small washer taped firmly to the outside of the chuck, running in the air |
| `bent_bit` | A slightly bent bit (a few degrees), running in the air. Not recorded so far |
| `loose_bit` | Chuck closed by hand only until the jaws touch the bit, running in the air. **Never into material** |
| `overheat` | Needs a verified thermistor and `--use-temp` |

## Recording real data

First check the board sends vibration (no flashing needed if it does):

```powershell
python -m ingest.ble_receiver --duration 60
```

`Vib window ...` lines every ~10 s mean it works. "Vibration characteristic not available" means the board is not running the machine1 firmware (or Windows has the old services cached: remove MachineGuard in Bluetooth settings and turn Bluetooth off and on). At rest the accelerometer should read about 1 g in total; ±4 g on every axis means the sensor wiring is loose.

Record one run per command. The run number is picked automatically, and there is a 3-second countdown: start the drill (or set up the fault) during it.

```powershell
python -m ml.record --session 1 --label normal_free --seconds 180 --cloud
python -m ml.record --session 1 --label loose_bit --seconds 180 --cloud --machine-id MG-001-TEST
```

`--cloud` uploads while recording. Use `--machine-id MG-001-TEST` for fault runs so they don't land in the real drill's history or trigger phone alerts. Recordings made without `--cloud` can be sent afterwards with `python -m ingest.upload_run data/raw` (`--dry-run` first lists what would be sent); the cloud skips anything it already has.

Each run is a folder written by the receiver: `windows/*.npz` (one Bluetooth window of 4096 samples each), `vibration.jsonl` and `temperature.jsonl`. The board sends a window every 10 s, so 180 s gives about 18 windows (about 126 analysis windows). The recorder warns if windows were lost or none arrived; delete that folder and record the run again.

**Keep every run consistent:** drill clamped to the table (not hand-held), same spot, same speed setting, sensor not touched during a session. Wear eye protection and stand out of line with the chuck for fault runs.

A session is one mounting of the sensor. Recording plan per session: `normal_free` ×3 (first), `off` ×2, `normal_load` ×3, then 3 runs of each fault.

### Checking recordings before training

Bad runs make a bad model. Move them out of `data/raw` (for example into `data/bad`) rather than deleting them:

- **No vibration saved** (for example the sensor wire came loose): skipped with a warning, but move them out anyway.
- **Drill not actually running:** if `live_check` says a `normal_*` run is **Machine off**, that run was recorded with the drill stopped. It teaches the model that normal includes a stopped drill and distorts the baseline.
- **Too few windows or clipping:** a 180 s run should have about 18 windows and no clipping. To list both for every run:

```powershell
python -c "import json,pathlib; [print(p.name, 'windows:', len(r), 'max clipped:', round(max([w.get('clipped_fraction') or 0 for w in r], default=0), 3)) for p in sorted(pathlib.Path('data/raw').glob('S*')) for r in [[json.loads(l) for l in open(p/'vibration.jsonl') if l.strip()] if (p/'vibration.jsonl').exists() else []]]"
```

## Training

With two or more sessions (the honest test: a session the model never saw, with the sensor re-mounted):

```powershell
python -m ml.train --raw data/raw --out models
```

With one session, test on held-out runs instead:

```powershell
python -m ml.train --raw data/raw --out models --holdout-run
```

`--holdout-run` holds out the last run of every label (for example `S1_normal_free_03`, `S1_off_02`), trains on the rest, and then repeats with each run number held out in turn ("leave-one-run-out"). The normal baseline is built only from training runs, never from a held-out one. A label with only one run is trained on but not tested. The score is optimistic because the test runs share the training runs' mounting.

What to look at in the output:

- **Isolation forest % flagged as unusual:** low for `normal_*`, high for faults. `off` is always high; the detector handles it separately.
- **Classifier accuracy and confusion table:** which labels get mixed up.
- **Leave-one-run-out:** one run scoring far lower than the others usually means a bad recording.

The final model is always trained on all the data and saved to `models/machineguard.joblib`.

## Checking the detector

```powershell
python -m ml.live_check --raw data/raw --session 1
```

Learns normal from the session's first `normal_free` run (the install step), then feeds every other run through the detector window by window and prints the most common status and how many windows would have cut the power. The first line shows the estimated running speed (RPM ÷ 60): check it matches the drill.

On the training data this mainly checks the live logic, since the model has already seen these runs. For a real test, record a few new runs into a separate folder and check those:

```powershell
python -m ml.record --session 2 --label normal_free --seconds 120 --out data/check
python -m ml.record --session 2 --label normal_load --seconds 120 --out data/check
python -m ml.record --session 2 --label loose_bit --seconds 120 --out data/check --machine-id MG-001-TEST
python -m ml.live_check --raw data/check --session 2
```

## Why analysis windows stay inside one Bluetooth window

Each Bluetooth window is 2.56 s of continuous samples, followed by a ~7.4 s gap.
An analysis window that crossed the gap would contain a jump that looks like an
impact (high kurtosis and crest factor, so a healthy run would look like
`loose_bit`). So each 4096-sample window is cut into 7 overlapping 1024-sample
analysis windows on its own.

## Temperature

Off by default. The thermistor values are unverified and it reads high (about 41 °C at
room temperature, most likely because GPIO1 is also the Heltec V3's battery-voltage pin),
so training on it would teach the model bad readings. Move it to a free ADC pin first. Once it is checked against a thermometer,
train with `python -m ml.train --use-temp`. A model trained with temperature needs
`temp_c` and `temp_slope_c_per_min` in `calibrate()` and `predict()`; one trained
without it ignores them. `overheat` runs need `--use-temp` to be learnable.

## Why the features are "relative"

Every session is compared to its own `normal_free` runs, the same way the device learns normal when it is clipped on. A 1x peak three times bigger than normal means imbalance whether the sensor sits a bit closer or further from the chuck. Kurtosis, crest factor and the 2x/1x ratio have no units, so they carry over as they are.

## How it works (the math, simplified)

Two models are used, both from scikit-learn: an **isolation forest** that learns what normal looks like, and a **random forest classifier** that learns which fault is which. Neither looks at raw vibration directly. Each analysis window (1,024 readings, 0.64 s) is first turned into 42 numbers, 14 per axis.

### Step 1: turning vibration into numbers

For each axis, the average is subtracted first. That removes gravity, which would otherwise swamp everything.

| Number | Formula (simplified) | What it tells you |
|---|---|---|
| **RMS** | √(average of x²) | Overall shaking strength |
| **Peak** | largest \|x\| | The biggest single jolt |
| **Crest factor** | peak ÷ RMS | Spikiness. A smooth sine wave is 1.41; knocks push it up |
| **Kurtosis** | average of (x − mean)⁴ ÷ (standard deviation)⁴ | How "impacty" the signal is. A sine wave is 1.5, random vibration about 3, rattling and knocking much higher |
| **Skew** | average of (x − mean)³ ÷ (standard deviation)³ | Whether jolts lean one way |
| **Band energy** | area under the spectrum between two frequencies | How much shaking sits in 2–40, 40–120 and 120–360 Hz |
| **Velocity RMS** | acceleration at each frequency ÷ (2π × frequency), combined | Vibration severity in mm/s, the industry measure (ISO 10816). Slow shaking counts for more than fast buzzing |
| **1×, 2×, 3× harmonics** | spectrum height at 1, 2 and 3 times the running speed | 1× grows with imbalance, 2× with a bent or misaligned bit |

The spectrum comes from an **FFT** (Fast Fourier Transform), which splits the 0.64 s of shaking into the frequencies it is made of. A Hann window is applied first so the abrupt start and end of the recording don't smear energy across the spectrum. With 1,600 readings per second and 1,024 readings per window, the spectrum has one value every 1,600 ÷ 1,024 ≈ **1.56 Hz**.

The **running speed** is estimated during calibration as the median of the strongest frequency across the normal windows. That is the drill's strongest vibration, which is not always the chuck's rotation (see the 110 Hz note under "Current model").

### Step 2: comparing to the machine's own normal

Every size-type number (RMS, peak, band energy, velocity, harmonics) is turned into a ratio against the median of the `normal_free` windows from the same mounting, on a log₂ scale:

```
relative = log₂( value ÷ normal value )
```

| relative | means |
|---|---|
| 0 | same as normal |
| +1 | twice normal |
| +1.5 | about 2.8 times normal |
| −1 | half of normal |
| −2 | a quarter of normal (the detector treats this as "machine off") |

Kurtosis, crest factor, skew and the 2×/1× and 3×/1× ratios have no units, so they are used as they are.

### Step 3: the isolation forest ("does this look normal?")

Trained only on `normal_free` and `normal_load` windows, so it needs no fault examples at all.

The idea: **an unusual point is easy to cut away from the crowd.** Picture every window as a dot among the 42 numbers. Pick a random number (say, y-axis RMS) and cut at a random value between its smallest and largest. Repeat on each side, again and again, until every dot sits alone. A dot in the middle of the crowd needs many cuts before it is alone; a dot far out on its own needs only a few.

The forest builds **300 such random trees**, each from 256 randomly chosen training windows, and averages how many cuts each new window needs (its *path length*):

```
score = 2 ^ ( − average path length ÷ typical path length )
```

The *typical path length* is what an ordinary point in a group of 256 needs, about 10 cuts. So:

- score near **0.5 or below**: as hard to isolate as everything else, so **normal**
- score **above 0.5**: isolated faster than usual, so **unusual**

The detector calls a window unusual when the score is above 0.5. (In the code, `decision_function` reports 0.5 − score, so below 0 means unusual.)

Its weakness shows in the current results: drilling into material shakes the drill a lot, so the "normal" crowd is spread out, and a loose bit spinning in the air lands inside it. That is why it flags only 16% of loose-bit windows.

### Step 4: the random forest classifier ("which fault is it?")

Trained on every label, including the faults and `off`.

A **decision tree** is a chain of yes/no questions, such as "is y-axis 120–360 Hz energy more than 1.5 times normal?". At each step it picks the question that best separates the labels, measured by *Gini impurity*:

```
impurity = 1 − (share of label A)² − (share of label B)² − …
```

A group that is all one label has impurity 0; a perfect mix of four labels has 0.75. The tree keeps splitting until the groups are nearly pure (at least 2 windows per final group).

One tree tends to memorise its training data, so the forest grows **300 trees**, each from a random resample of the windows and only allowed to consider a random handful of the 42 numbers (√42, about 6) at each question. Each tree votes, and the **share of votes is the confidence**: 270 of 300 trees saying `loose_bit` is 90%.

Labels with fewer windows get extra weight (`class_weight="balanced"`: weight = total windows ÷ (number of labels × windows of that label)), so a label recorded less often isn't drowned out.

The "Top 10 features" list that `train.py` prints is how much each number reduced impurity on average across all trees: the numbers the classifier leans on most.

### Step 5: simple physics rules

As a second opinion, the detector checks the strongest axis against fixed rules (starting values, to be tuned on real fault recordings):

| Rule | Points to |
|---|---|
| kurtosis > 5 or crest factor > 5 | `loose_bit` (rattling, knocks) |
| 2× more than 2.8 times normal, and 2× at least half the size of 1× | `bent_bit` |
| 1× more than 2.8 times normal | `imbalance` |

A fault is only declared when the classifier is at least 60% sure **and** either the isolation forest or these rules agree (see "How the detector decides").

## How the detector decides

Each window gets a status:

1. Main axis under a quarter of its normal level, or the classifier says `off`: **Machine off**.
2. Classifier says normal and the isolation forest agrees: **Running normally**.
3. Classifier names a fault with at least 60% confidence, and either the isolation forest or the FFT rules agree: **Fault**.
4. Anything else: **Unusual vibration, type unknown**. It warns but does not shut off.

`shutoff` is only true when at least 4 of the last 7 windows were faults (`SHUTOFF_K` / `SHUTOFF_N` in `settings.py`), so one bump never cuts the power. Only **Fault** windows count; **Unusual vibration** never does. Call `reset_shutoff()` after the machine is restarted.

Requiring two checks to agree before a fault is deliberate: a confident classifier alone cannot cut the power. The cost, with the current model, is that the loose bit stays at **Unusual vibration** (see "Current model").

## Testing honestly

Windows from the same run look almost identical, so `train.py` never tests on a random split of windows: that would inflate the score. It tests on a whole session it never trained on (sensor re-mounted) or, with `--holdout-run`, on whole runs it never trained on.

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
