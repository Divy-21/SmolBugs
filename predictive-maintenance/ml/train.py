"""Trains both models and reports how well they work on a session they never saw.

    python -m ml.train --raw data/raw                 (vibration only)
    python -m ml.train --raw data/raw --use-temp      (only once the thermistor is verified)
    python -m ml.train --raw data/raw --holdout-run   (one session: test on each label's last run)

Steps:
1. Features for every analysis window of every run (uses the team's FFT code).
2. Each session is compared to its own normal_free runs ("learn normal" on install).
3. Isolation forest learns what normal looks like (normal_free + normal_load only).
4. Random forest learns which fault is which.
5. Tested on a held-out session (sensor removed and re-mounted), never a random split.
   With --holdout-run it is tested on the last run of every label instead, so one session
   is enough. That score is optimistic: the test runs share the training runs' mounting.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report

from .detector import MachineGuardDetector
from .features import make_baseline, relative_features
from .recordings import build_feature_table
from .settings import BASELINE_LABEL, LABELS, NORMAL_LABELS

META = ["session", "label", "run", "file", "window_id", "window_start_s"]


def add_relative(table: pd.DataFrame, use_for_baseline: pd.Series | None = None) -> pd.DataFrame:
    """Features relative to each session's own normal_free windows. Keeps table's index.

    use_for_baseline: which rows may form the baseline (held-out runs must not), or None for all.
    """
    parts = []
    for session, df in table.groupby("session"):
        normal = df[df["label"] == BASELINE_LABEL]
        if use_for_baseline is not None:
            normal = normal[use_for_baseline.loc[normal.index]]
        if normal.empty:
            print(f"skipping session {session}: no {BASELINE_LABEL} runs to learn normal from")
            continue
        baseline = make_baseline(normal.drop(columns=META))
        rel = relative_features(df.drop(columns=META), baseline)
        parts.append(pd.concat([df[META], rel], axis=1))
    if not parts:
        raise SystemExit(f"no session has {BASELINE_LABEL} runs to learn normal from")
    return pd.concat(parts)


def runs_per_label(table: pd.DataFrame) -> pd.Series:
    """For every row, how many runs its (session, label) has."""
    return table.groupby(["session", "label"])["run"].transform("nunique")


def run_mask(table: pd.DataFrame, run: int | None = None) -> pd.Series:
    """Rows to hold out: run number `run` (default: each label's last run) of every
    (session, label) that has at least 2 runs, so every label stays in training."""
    target = table.groupby(["session", "label"])["run"].transform("max") if run is None else run
    return (table["run"] == target) & (runs_per_label(table) >= 2)


def split_by_runs(table: pd.DataFrame, test_rows: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(train, test) relative features; the baseline never sees a held-out run."""
    data = add_relative(table, use_for_baseline=~test_rows)
    held = test_rows.loc[data.index]
    return data[~held], data[held]


def new_iforest():
    return IsolationForest(n_estimators=300, contamination="auto", random_state=0)


def new_classifier():
    return RandomForestClassifier(n_estimators=300, class_weight="balanced", min_samples_leaf=2, random_state=0)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", default="data/raw", help="folder of run folders like S1_imbalance_03")
    p.add_argument("--out", default="models", help="where to save the model and reports")
    p.add_argument("--test-session", type=int, default=None, help="held-out session (default: the last one)")
    p.add_argument("--use-temp", action="store_true",
                   help="include temperature features (leave off until the thermistor is verified)")
    p.add_argument("--holdout-run", action="store_true",
                   help="test on the last run of every label instead of a held-out session "
                        "(works with one session; optimistic, since the sensor was never re-mounted)")
    args = p.parse_args(argv)
    if args.holdout_run and args.test_session is not None:
        p.error("use either --holdout-run or --test-session, not both")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    table = build_feature_table(args.raw, use_temp=args.use_temp)
    table.to_csv(out / "features_raw.csv", index=False)
    data = add_relative(table)
    data.to_csv(out / "features_relative.csv", index=False)
    feature_cols = [c for c in data.columns if c not in META]

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    sessions = sorted(int(s) for s in data["session"].unique())
    if args.holdout_run:
        test_rows = run_mask(table)
        if not test_rows.any():
            raise SystemExit("--holdout-run needs at least 2 runs of a label in a session to hold one out")
        for (session, label), n in table.groupby(["session", "label"])["run"].nunique().items():
            if n < 2:
                print(f"note: S{session} {label} has only 1 run, so it is trained on but not tested")
        train, test = split_by_runs(table, test_rows)
        held = sorted(table.loc[test_rows, "file"].unique())
        print("\nTEST ON HELD-OUT RUNS (same mounting as training, so expect an optimistic score):")
        print("  " + ", ".join(held))
        print(f"train {len(train)} windows, test {len(test)} windows")
    else:
        if len(sessions) < 2:
            raise SystemExit(f"need at least 2 sessions with {BASELINE_LABEL} runs to test on an unseen one, "
                             f"found {sessions} (or add --holdout-run to test on held-out runs instead)")
        test_session = args.test_session or sessions[-1]
        if test_session not in sessions:
            raise SystemExit(f"test session {test_session} not found; sessions are {sessions}")
        train, test = data[data["session"] != test_session], data[data["session"] == test_session]
        print(f"\ntrain sessions {[s for s in sessions if s != test_session]}, test session {test_session}")
        print(f"train {len(train)} windows, test {len(test)} windows")

    # ---------- 1. anomaly detection ----------
    iforest = new_iforest().fit(train[train["label"].isin(NORMAL_LABELS)][feature_cols])
    flagged = pd.Series(iforest.predict(test[feature_cols]) == -1, index=test.index)
    print("\n=== Isolation forest: % of test windows flagged as unusual ===")
    print("(want low for normal_*, high for faults; 'off' is handled separately by the detector)")
    for label in LABELS:
        mask = test["label"] == label
        if mask.any():
            print(f"  {label:<12} {100 * flagged[mask].mean():5.1f}%")

    # ---------- 2. fault classifier ----------
    clf = new_classifier().fit(train[feature_cols], train["label"])
    pred = clf.predict(test[feature_cols])
    unseen = "held-out runs" if args.holdout_run else f"unseen session {test_session}"
    print(f"\n=== Classifier on {unseen}: accuracy {accuracy_score(test['label'], pred):.1%} ===")
    print(classification_report(test["label"], pred, zero_division=0))
    cm = pd.crosstab(test["label"], pred, rownames=["actual"], colnames=["predicted"])
    print(cm)
    cm.to_csv(out / "confusion_matrix.csv")

    # Leave-one-run-out: every run number takes a turn as the held-out one
    if args.holdout_run:
        print("\n=== Leave-one-run-out accuracy ===")
        for run in sorted(int(r) for r in table["run"].unique()):
            rows = run_mask(table, run)
            if not rows.any():
                continue
            tr, te = split_by_runs(table, rows)
            acc = accuracy_score(te["label"], new_classifier().fit(tr[feature_cols], tr["label"]).predict(te[feature_cols]))
            print(f"  run {run:02d} held out: {acc:.1%}")

    # Leave-one-session-out: every session takes a turn as the unseen one
    elif len(sessions) > 2:
        print("\n=== Leave-one-session-out accuracy ===")
        for s in sessions:
            tr, te = data[data["session"] != s], data[data["session"] == s]
            acc = accuracy_score(te["label"], new_classifier().fit(tr[feature_cols], tr["label"]).predict(te[feature_cols]))
            print(f"  session {s} held out: {acc:.1%}")

    importance = pd.Series(clf.feature_importances_, index=feature_cols).sort_values(ascending=False)
    print("\n=== Top 10 features the classifier relies on ===")
    print(importance.head(10).round(3).to_string())

    # ---------- 3. final models on everything, for the device ----------
    final = MachineGuardDetector(
        new_iforest().fit(data[data["label"].isin(NORMAL_LABELS)][feature_cols]),
        new_classifier().fit(data[feature_cols], data["label"]),
        feature_cols,
        meta={"sessions": sessions, "uses_temperature": args.use_temp,
              "windows": int(len(data)), "labels": sorted(data["label"].unique())},
    )
    final.save(out / "machineguard.joblib")
    print(f"\nsaved {out / 'machineguard.joblib'} (trained on all sessions)")


if __name__ == "__main__":
    main()