"""Trains both models and reports how well they work on a session they never saw.

    python -m ml.train --raw data/fake/raw        (fake data, works today)
    python -m ml.train --raw data/raw             (your real recordings)

Steps:
1. Features for every 1 s window of every run (uses the team's FFT code).
2. Each session is compared to its own normal_free runs ("learn normal" on install).
3. Isolation forest learns what normal looks like (normal_free + normal_load only).
4. Random forest learns which fault is which.
5. Tested on a held-out session (sensor removed and re-mounted), never a random split.
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

META = ["session", "label", "run", "file", "window_start_s"]


def add_relative(table: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for session, df in table.groupby("session"):
        normal = df[df["label"] == BASELINE_LABEL]
        if normal.empty:
            print(f"skipping session {session}: no {BASELINE_LABEL} runs to learn normal from")
            continue
        baseline = make_baseline(normal.drop(columns=META))
        rel = relative_features(df.drop(columns=META), baseline)
        parts.append(pd.concat([df[META], rel], axis=1))
    return pd.concat(parts, ignore_index=True)


def new_iforest():
    return IsolationForest(n_estimators=300, contamination="auto", random_state=0)


def new_classifier():
    return RandomForestClassifier(n_estimators=300, class_weight="balanced", min_samples_leaf=2, random_state=0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", default="data/raw", help="folder of recordings like S1_imbalance_03.csv")
    p.add_argument("--out", default="models", help="where to save the model and reports")
    p.add_argument("--test-session", type=int, default=None, help="held-out session (default: the last one)")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    table = build_feature_table(args.raw)
    table.to_csv(out / "features_raw.csv", index=False)
    data = add_relative(table)
    data.to_csv(out / "features_relative.csv", index=False)
    feature_cols = [c for c in data.columns if c not in META]

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    sessions = sorted(int(s) for s in data["session"].unique())
    test_session = args.test_session or sessions[-1]
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
    print(f"\n=== Classifier on unseen session {test_session}: accuracy {accuracy_score(test['label'], pred):.1%} ===")
    print(classification_report(test["label"], pred, zero_division=0))
    cm = pd.crosstab(test["label"], pred, rownames=["actual"], colnames=["predicted"])
    print(cm)
    cm.to_csv(out / "confusion_matrix.csv")

    # Leave-one-session-out: every session takes a turn as the unseen one
    if len(sessions) > 2:
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
    )
    final.save(out / "machineguard.joblib")
    print(f"\nsaved {out / 'machineguard.joblib'} (trained on all sessions)")


if __name__ == "__main__":
    main()
