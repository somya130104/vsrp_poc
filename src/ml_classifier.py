"""
ml_classifier.py
================
XGBoost port-skip classifier.

Inspired by:
  "Skip or not: Hybrid machine learning for decision support in strategic
   port-skipping behavior to enhance liner shipping reliability" (2025)
  Ocean Engineering — hybrid CLPSO-XGBoost, 86% accuracy, 88% precision.

Our implementation:
  - Features extracted from simulation logs (delay, queue, TEU, distance, etc.)
  - XGBoost binary classifier (skip=1 / no-skip=0)
  - Train on synthetic simulation data, validate 80/20 split
  - Outputs: accuracy, precision, recall, feature importances, SHAP-style bars
"""

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score,
    f1_score, roc_auc_score, classification_report, confusion_matrix
)
from sklearn.preprocessing import StandardScaler
import xgboost as xgb
import json, os

FEATURES = [
    "delay_hrs",
    "queue_length",
    "vessel_teu",
    "leg_dist_nm",
    "hist_skip_rate",
    "handling_efficiency",
    "fuel_price_usd_mt",
    "days_since_disruption",
]
LABEL = "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# LOAD & TRAIN
# ─────────────────────────────────────────────────────────────────────────────
def load_data(path: str = "data/ml_training_data.csv") -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"ML training data not found at {path}. Run data_generator.py first.")
    return pd.read_csv(path)


def train_classifier(df: pd.DataFrame):
    X = df[FEATURES].values
    y = df[LABEL].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # XGBoost with class balance
    scale_pos = int((y_train == 0).sum() / max(1, (y_train == 1).sum()))
    model = xgb.XGBClassifier(
        n_estimators=150,
        max_depth=5,
        learning_rate=0.08,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos,
        eval_metric="logloss",
        verbosity=0,
        random_state=42,
    )
    model.fit(X_train, y_train)

    y_pred      = model.predict(X_test)
    y_prob      = model.predict_proba(X_test)[:, 1]

    metrics = {
        "accuracy"  : round(float(accuracy_score(y_test, y_pred)), 4),
        "precision" : round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall"    : round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1"        : round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
        "roc_auc"   : round(float(roc_auc_score(y_test, y_prob)), 4),
        "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
        "n_train"   : int(len(X_train)),
        "n_test"    : int(len(X_test)),
        "skip_rate" : round(float(y.mean()), 4),
    }

    # Feature importances
    importances = model.feature_importances_
    feat_imp = {f: round(float(importances[i]), 4) for i, f in enumerate(FEATURES)}
    feat_imp_sorted = dict(sorted(feat_imp.items(), key=lambda x: x[1], reverse=True))

    print("\n=== XGBoost Skip Classifier ===")
    print(f"  Accuracy  : {metrics['accuracy']:.2%}")
    print(f"  Precision : {metrics['precision']:.2%}")
    print(f"  Recall    : {metrics['recall']:.2%}")
    print(f"  F1-Score  : {metrics['f1']:.2%}")
    print(f"  ROC-AUC   : {metrics['roc_auc']:.4f}")
    print("\n  Feature Importances:")
    for k, v in feat_imp_sorted.items():
        bar = "█" * int(v * 40)
        print(f"    {k:<28} {bar}  {v:.4f}")

    return model, metrics, feat_imp_sorted


def predict_skip_probability(model, features_dict: dict) -> float:
    """Given a dict of feature values, return skip probability [0–1]."""
    row = np.array([[features_dict.get(f, 0.0) for f in FEATURES]])
    return float(model.predict_proba(row)[0, 1])


def run_and_save():
    df              = load_data()
    model, metrics, feat_imp = train_classifier(df)

    # Save metrics + importances as JSON for dashboard
    results = {
        "metrics"     : metrics,
        "feature_importances": feat_imp,
        "features"    : FEATURES,
    }
    with open("data/ml_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\n✓ Saved ML results to data/ml_results.json")
    return model, results


if __name__ == "__main__":
    model, results = run_and_save()

    # Demo prediction
    demo = {
        "delay_hrs"           : 130,
        "queue_length"        : 7,
        "vessel_teu"          : 14000,
        "leg_dist_nm"         : 386,
        "hist_skip_rate"      : 0.146,
        "handling_efficiency" : 0.5,
        "fuel_price_usd_mt"   : 650,
        "days_since_disruption": 30,
    }
    p = predict_skip_probability(model, demo)
    print(f"\n  Demo prediction (Oakland, delay=130h, congestion=high): Skip probability = {p:.1%}")
