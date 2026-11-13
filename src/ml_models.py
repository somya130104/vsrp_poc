"""
ml_models.py
============
Two learning components.

ML-1  Delay predictor (always TESTED on real vessels)
      Predicts the arrival delay at the next port from the departure delay at
      the current port and the planned leg. Six regressors (ridge, k-NN, random
      forest, gradient boosting, XGBoost, MLP), each trained on (a) the other
      real vessels only and (b) those plus the synthetic year (synthetic.py).
      Leave-one-real-vessel-out, against a "delay persists" naive forecast.

ML-2  Recovery advisor (learning to optimise)
      Real data contains only three omitted calls — far too few to learn
      skipping from. Instead, thousands of scenarios are sampled from the real
      delay and berth-congestion distributions, each is solved to optimality
      with the MILP, and ten classifiers (logistic regression, naive Bayes,
      k-NN, decision tree, SVM, MLP, random forest, extra trees, sklearn
      gradient boosting, XGBoost) learn which calls the MILP omits.
      At run time the classifier fixes the skip decisions and a much smaller
      MILP fills in speeds ("ML-guided MILP"). We report classification
      metrics AND regret (extra cost vs. the exact optimum).
"""

import time
import numpy as np

from .routes import SERVICES, PARAMS, dist_nm
from .recovery import build_scenario, solve_milp, plan_rule, plan_baseline, MANOEUVRE_H

FEATURES = [
    "svc_tp8", "d0_h", "node_pos", "planned_h_to_port", "min_h_to_port",
    "sprint_late_h", "berth_late_h", "berth_late_before_h", "berth_late_after_h",
    "port_teu", "berth_h", "skip_cost_k", "downstream_rate_k",
    "bunker_usd_t", "delay_usd_teu_day", "rollover_usd_teu",
]
FEATURE_LABELS = {
    "svc_tp8": "Service (TP8 = 1)",
    "d0_h": "Current delay",
    "node_pos": "Position in horizon",
    "planned_h_to_port": "Planned hours to port",
    "min_h_to_port": "Fastest hours to port",
    "sprint_late_h": "Late even at full speed",
    "berth_late_h": "Berth delay at this port",
    "berth_late_before_h": "Berth delay before",
    "berth_late_after_h": "Berth delay after",
    "port_teu": "Cargo at port (TEU)",
    "berth_h": "Planned berth hours",
    "skip_cost_k": "Rollover cost if skipped",
    "downstream_rate_k": "Downstream delay cost",
    "bunker_usd_t": "Bunker price",
    "delay_usd_teu_day": "Delay cost per TEU-day",
    "rollover_usd_teu": "Rollover cost per TEU",
}


# ─────────────────────────────────────────────────────────────────────────────
# EMPIRICAL DISTRIBUTIONS FROM REAL DATA
# ─────────────────────────────────────────────────────────────────────────────
def empirical(real):
    dep = {s: [] for s in SERVICES}
    for c in real["calls"]:
        if c["service"] in SERVICES and c["dep_delay_h"] is not None:
            dep[c["service"]].append(max(0.0, c["dep_delay_h"]))
    berth = {}
    for lg in real["legs"]:
        added = (lg["arr_delay_h"] or 0) - max(0.0, lg["dep_delay_h"] or 0)
        congested = lg["est_wait_h"] > 12 and added > 12 and (lg["arr_delay_h"] or 0) > 12
        berth.setdefault((lg["service"], lg["to"]), []).append(lg["arr_delay_h"] if congested else 0.0)
    return {"dep_delay": dep, "berth_late": {f"{k[0]}|{k[1]}": v for k, v in berth.items()}}


def sample_scenario(rng, real, emp):
    svc = "TP8" if rng.random() < 0.7 else "TP7"
    rot = SERVICES[svc]["rotation"]
    start = rot[rng.integers(len(rot))]
    pool = emp["dep_delay"][svc]
    if rng.random() < 0.25 or not pool:
        d0 = float(rng.uniform(0, 400))
    else:
        base = pool[rng.integers(len(pool))]
        d0 = float(np.clip(base + rng.normal(0, 0.15 * base + 10), 0, 450))
    bl = {}
    for p in rot:
        if p == start:
            continue
        vals = emp["berth_late"].get(f"{svc}|{p}", [0.0])
        v = vals[rng.integers(len(vals))]
        if v > 0:
            bl[p] = float(np.clip(v + rng.normal(0, 0.2 * v), 0, 500))
    params = {
        "bunker_usd_t": float(rng.uniform(450, 750)),
        "delay_usd_teu_day": float(rng.uniform(10, 35)),
        "rollover_usd_teu": float(rng.uniform(250, 650)),
    }
    return build_scenario(real["proforma"], svc, start, d0, bl, params)


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES  (one row per skippable call in a scenario)
# ─────────────────────────────────────────────────────────────────────────────
def node_features(scn):
    P, nodes = scn["params"], scn["nodes"]
    N = len(nodes) - 1
    rows, idx = [], []
    t_fast = scn["d0"]
    fast = {}
    for j in range(1, N + 1):
        D = dist_nm(nodes[j - 1]["port"], nodes[j]["port"])
        t_fast += D / P["v_max"] + MANOEUVRE_H
        fast[j] = t_fast
        t_fast = max(t_fast, nodes[j]["open"]) + nodes[j]["stay"] * (1 - P["compress_max"])
    rate = lambda k: nodes[k]["teu"] * P["delay_usd_teu_day"] / 24.0
    for j in range(1, N + 1):
        nd = nodes[j]
        if nd["mandatory"]:
            continue
        bl = nd["berth_late"]
        before = max([nodes[k]["berth_late"] for k in range(1, j)] or [0.0])
        after = max([nodes[k]["berth_late"] for k in range(j + 1, N + 1)] or [0.0])
        rows.append([
            1.0 if scn["service"] == "TP8" else 0.0,
            scn["d0"], j / N, nd["pln_arr"], fast[j] - scn["d0"],
            max(0.0, fast[j] - nd["pln_arr"]), bl, before, after,
            nd["teu"], nd["stay"],
            (nd["teu"] * P["rollover_usd_teu"] - P["port_fee_usd"]) / 1000.0,
            sum(rate(k) for k in range(j, N + 1)) / 1000.0,
            P["bunker_usd_t"], P["delay_usd_teu_day"], P["rollover_usd_teu"],
        ])
        idx.append(j)
    return np.array(rows, dtype=float), idx


def generate_dataset(real, n=2500, seed=7, log_every=500):
    rng = np.random.default_rng(seed)
    emp = empirical(real)
    X, y, groups, scns, sols = [], [], [], [], []
    t0 = time.time()
    for s in range(n):
        scn = sample_scenario(rng, real, emp)
        sol = solve_milp(scn)
        if sol is None:
            continue
        feats, idx = node_features(scn)
        sid = len(scns)
        visited_nodes = {t["node"] for t in sol["timeline"] if t["visited"]}
        for f, j in zip(feats, idx):
            X.append(f)
            y.append(0 if j in visited_nodes else 1)
            groups.append(sid)
        scns.append(scn)
        sols.append(sol)
        if log_every and (s + 1) % log_every == 0:
            print(f"    {s + 1}/{n} scenarios solved ({time.time() - t0:.0f}s)")
    return np.array(X), np.array(y), np.array(groups), scns, sols, emp


# ─────────────────────────────────────────────────────────────────────────────
# ML-2 : RECOVERY ADVISOR
# ─────────────────────────────────────────────────────────────────────────────
def ml_visits(model, scn, threshold=0.5):
    feats, idx = node_features(scn)
    N = len(scn["nodes"]) - 1
    if len(idx) == 0:
        return list(range(1, N + 1)), {}
    prob = model.predict_proba(feats)[:, 1]
    probs = {j: float(p) for j, p in zip(idx, prob)}
    skip = {j for j, p in probs.items() if p >= threshold}
    # enforce max consecutive omissions
    K, run, visits = scn["params"]["max_consec_skips"], 0, []
    for j in range(1, N + 1):
        if j in skip and run < K:
            run += 1
        else:
            visits.append(j)
            run = 0
    return visits, probs


CLASSIFIERS = [
    ("logistic", "Logistic regression"),
    ("naive_bayes", "Naive Bayes"),
    ("knn", "k-nearest neighbours"),
    ("tree", "Decision tree"),
    ("svm", "Support vector machine"),
    ("mlp", "Neural network (MLP)"),
    ("random_forest", "Random forest"),
    ("extra_trees", "Extra trees"),
    ("hist_gb", "Gradient boosting (sklearn)"),
    ("xgboost", "XGBoost"),
]


def _make_classifier(key, spw, seed):
    from sklearn.linear_model import LogisticRegression
    from sklearn.naive_bayes import GaussianNB
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.svm import SVC
    from sklearn.neural_network import MLPClassifier
    from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from xgboost import XGBClassifier
    sc = StandardScaler
    return {
        "logistic": lambda: make_pipeline(sc(), LogisticRegression(max_iter=3000, class_weight="balanced")),
        "naive_bayes": lambda: make_pipeline(sc(), GaussianNB()),
        "knn": lambda: make_pipeline(sc(), KNeighborsClassifier(n_neighbors=15, weights="distance")),
        "tree": lambda: DecisionTreeClassifier(max_depth=8, min_samples_leaf=5, class_weight="balanced",
                                               random_state=seed),
        "svm": lambda: make_pipeline(sc(), SVC(C=3.0, gamma="scale", probability=True, class_weight="balanced",
                                               random_state=seed)),
        "mlp": lambda: make_pipeline(sc(), MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-3, max_iter=600,
                                                         early_stopping=True, random_state=seed)),
        "random_forest": lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=2, n_jobs=-1,
                                                        class_weight="balanced_subsample", random_state=seed),
        "extra_trees": lambda: ExtraTreesClassifier(n_estimators=300, min_samples_leaf=2, n_jobs=-1,
                                                    class_weight="balanced_subsample", random_state=seed),
        "hist_gb": lambda: HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_depth=6,
                                                          class_weight="balanced", random_state=seed),
        "xgboost": lambda: XGBClassifier(n_estimators=350, max_depth=5, learning_rate=0.06, subsample=0.9,
                                         colsample_bytree=0.9, scale_pos_weight=spw, eval_metric="logloss",
                                         random_state=seed),
    }[key]()


def train_advisor(real, n=2500, seed=7, svm_max_rows=5000):
    from sklearn.metrics import (accuracy_score, precision_score, recall_score, f1_score,
                                 roc_auc_score, confusion_matrix)

    X, y, g, scns, sols, emp = generate_dataset(real, n=n, seed=seed)
    rng = np.random.default_rng(seed + 1)
    scen_ids = np.unique(g)
    test_ids = set(rng.choice(scen_ids, size=int(0.2 * len(scen_ids)), replace=False).tolist())
    te = np.array([gi in test_ids for gi in g])
    Xtr, ytr, Xte, yte = X[~te], y[~te], X[te], y[te]
    pos = max(1, ytr.sum())
    spw = float((len(ytr) - pos) / pos)
    test_list = sorted(test_ids)

    def metrics(model):
        t = time.perf_counter()
        p = model.predict_proba(Xte)[:, 1]
        pred_us = (time.perf_counter() - t) * 1e6 / max(1, len(Xte))
        yh = (p >= 0.5).astype(int)
        cm = confusion_matrix(yte, yh, labels=[0, 1])
        return {"accuracy": accuracy_score(yte, yh), "precision": precision_score(yte, yh, zero_division=0),
                "recall": recall_score(yte, yh, zero_division=0), "f1": f1_score(yte, yh, zero_division=0),
                "roc_auc": roc_auc_score(yte, p), "confusion": cm.tolist(), "predict_us_per_call": pred_us}

    # exact optimum + rule baseline for every held-out scenario (shared by all models)
    rule_regrets, milp_ms = [], []
    for sid in test_list:
        opt = sols[sid]
        rr = plan_rule(scns[sid])
        rule_regrets.append(100 * (rr["total"] - opt["total"]) / opt["total"])
        milp_ms.append(opt["solve_ms"])
    rule_regrets = np.array(rule_regrets)

    def regret(model):
        """Fix the model's skips, let a small MILP set speeds, compare with the optimum."""
        cache, regs, ms, match = {}, [], [], 0
        for sid in test_list:
            scn, opt = scns[sid], sols[sid]
            t = time.perf_counter()
            visits, _ = ml_visits(model, scn)
            key = (sid, tuple(visits))
            if key not in cache:
                cache[key] = solve_milp(scn, fixed_visits=visits)
            mlp = cache[key]
            ms.append((time.perf_counter() - t) * 1000)
            if mlp is None:
                regs.append(np.nan)
                continue
            regs.append(100 * (mlp["total"] - opt["total"]) / opt["total"])
            if sorted(mlp["skipped"]) == sorted(opt["skipped"]):
                match += 1
        regs = np.array(regs)
        ok = regs[~np.isnan(regs)]
        return ok, {"mean_pct": float(ok.mean()), "median_pct": float(np.median(ok)),
                    "p95_pct": float(np.percentile(ok, 95)),
                    "zero_regret_share": float((ok < 0.01).mean()),
                    "exact_skip_match": match / max(1, len(ok)),
                    "infeasible": int(np.isnan(regs).sum()),
                    "guided_ms": float(np.mean(ms))}

    fitted, board = {}, []
    for key, name in CLASSIFIERS:
        model = _make_classifier(key, spw, seed)
        Xf, yf = Xtr, ytr
        if key == "svm" and len(ytr) > svm_max_rows:         # SVM scales badly: train on a subsample
            sub = np.random.default_rng(seed).choice(len(ytr), svm_max_rows, replace=False)
            Xf, yf = Xtr[sub], ytr[sub]
        t = time.perf_counter()
        model.fit(Xf, yf)
        train_s = time.perf_counter() - t
        m = metrics(model)
        regs, rg = regret(model)
        fitted[key] = (model, regs, m)
        board.append({"key": key, "name": name, "train_s": round(train_s, 2), "train_rows": int(len(yf)),
                      **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()},
                      "regret": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in rg.items()}})
        print(f"    {name:<28} acc {m['accuracy']:.3f}  F1 {m['f1']:.3f}  AUC {m['roc_auc']:.3f}  "
              f"regret {rg['mean_pct']:.2f}%  exact {100 * rg['exact_skip_match']:.0f}%  ({train_s:.1f}s)")
    board.sort(key=lambda r: (r["regret"]["mean_pct"], -r["f1"]))
    best = board[0]["key"]

    xgb, regrets, m_xgb = fitted["xgboost"]
    m_log = fitted["logistic"][2]
    xr = next(b for b in board if b["key"] == "xgboost")["regret"]

    imp = xgb.get_booster().get_score(importance_type="gain")
    gains = {FEATURES[int(k[1:])]: v for k, v in imp.items()}
    tot = sum(gains.values()) or 1.0
    importance = sorted(
        [{"feature": f, "label": FEATURE_LABELS[f], "gain": round(gains.get(f, 0.0) / tot, 4)} for f in FEATURES],
        key=lambda r: -r["gain"])

    hist_edges = [0, 0.5, 1, 2, 5, 10, 20, 50, 1000]
    hist = np.histogram(np.clip(regrets, 0, 999), bins=hist_edges)[0].tolist()
    rhist = np.histogram(np.clip(rule_regrets, 0, 999), bins=hist_edges)[0].tolist()

    summary = {
        "n_scenarios": int(len(scns)), "n_rows": int(len(y)),
        "n_train_rows": int(len(ytr)), "n_test_rows": int(len(yte)),
        "n_test_scenarios": int(len(test_list)),
        "skip_rate": float(y.mean()), "scale_pos_weight": round(spw, 2),
        "xgboost": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m_xgb.items()},
        "logistic": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m_log.items()},
        "models": board, "best_model": best,
        "rule": {"mean_pct": round(float(rule_regrets.mean()), 3),
                 "median_pct": round(float(np.median(rule_regrets)), 3)},
        "importance": importance,
        "regret": {
            "mean_pct": round(xr["mean_pct"], 3),
            "median_pct": round(xr["median_pct"], 3),
            "p95_pct": round(xr["p95_pct"], 3),
            "zero_regret_share": xr["zero_regret_share"],
            "exact_skip_match": xr["exact_skip_match"],
            "rule_mean_pct": round(float(rule_regrets.mean()), 3),
            "rule_median_pct": round(float(np.median(rule_regrets)), 3),
            "hist_edges": hist_edges, "hist": hist, "rule_hist": rhist,
        },
        "timing": {"milp_ms": round(float(np.mean(milp_ms)), 2), "ml_guided_ms": round(xr["guided_ms"], 2)},
        "empirical": emp,
    }
    return xgb, summary


# ─────────────────────────────────────────────────────────────────────────────
# ML-1 : NEXT-PORT DELAY PREDICTOR  (real data)
# ─────────────────────────────────────────────────────────────────────────────
REGRESSORS = [
    ("ridge", "Linear (ridge)"),
    ("knn", "k-nearest neighbours"),
    ("random_forest", "Random forest"),
    ("gboost", "Gradient boosting"),
    ("xgboost", "XGBoost"),
    ("mlp", "Neural network (MLP)"),
]


def _make_regressor(key, seed=0):
    from sklearn.linear_model import Ridge
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    from sklearn.neural_network import MLPRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from xgboost import XGBRegressor
    return {
        "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=3.0)),
        "knn": lambda: make_pipeline(StandardScaler(), KNeighborsRegressor(n_neighbors=7, weights="distance")),
        "random_forest": lambda: RandomForestRegressor(n_estimators=300, min_samples_leaf=3, n_jobs=-1,
                                                       random_state=seed),
        "gboost": lambda: GradientBoostingRegressor(n_estimators=250, max_depth=3, learning_rate=0.05,
                                                    subsample=0.9, loss="huber", random_state=seed),
        "xgboost": lambda: XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9,
                                        colsample_bytree=0.9, random_state=seed),
        "mlp": lambda: make_pipeline(StandardScaler(), MLPRegressor(hidden_layer_sizes=(32, 16), alpha=1e-2,
                                                                    max_iter=3000, early_stopping=False,
                                                                    random_state=seed)),
    }[key]()


def train_delay_model(real, synthetic_legs=None):
    """Next-port arrival delay. Always TESTED on real vessels (leave-one-vessel-out);
    trained either on the other real vessels only, or on them + the synthetic year."""

    def usable(ls):
        return [l for l in ls if l["dep_delay_h"] is not None and l["arr_delay_h"] is not None
                and l["pln_h"] is not None]

    legs = usable(real["legs"])
    syn = usable(synthetic_legs or [])
    ports = sorted({l["to"] for l in legs + syn})
    froms = sorted({l["from"] for l in legs + syn})

    def feat(l):
        row = [max(0.0, l["dep_delay_h"]), l["pln_h"], l["dist_nm"] / 1000.0,
               l["pln_h"] - l["dist_nm"] / 18.0, 1.0 if l["service"] == "TP8" else 0.0]
        row += [1.0 if l["to"] == p else 0.0 for p in ports]
        row += [1.0 if l["from"] == p else 0.0 for p in froms]
        return row

    X = np.array([feat(l) for l in legs])
    y = np.array([l["arr_delay_h"] for l in legs])
    Xs = np.array([feat(l) for l in syn]) if syn else np.zeros((0, X.shape[1]))
    ys = np.array([l["arr_delay_h"] for l in syn])
    vessels = np.array([l["vessel"] for l in legs])
    preds_naive = np.array([max(0.0, l["dep_delay_h"]) for l in legs])
    cong = np.array([l["to"] == "CNSHA" for l in legs])

    def mae(p, mask=None):
        m = np.ones(len(y), bool) if mask is None else mask
        return float(np.mean(np.abs(p[m] - y[m]))) if m.any() else None

    regimes = [("real", "Real only")] + ([("real_syn", "Real + synthetic")] if syn else [])
    board, preds = [], {}
    for key, name in REGRESSORS:
        for reg, reg_name in regimes:
            p = np.zeros(len(y))
            for v in np.unique(vessels):
                tr, te = vessels != v, vessels == v
                Xt, yt = X[tr], y[tr]
                if reg == "real_syn":
                    Xt, yt = np.vstack([Xt, Xs]), np.concatenate([yt, ys])
                m = _make_regressor(key).fit(Xt, yt)
                p[te] = m.predict(X[te])
            preds[(key, reg)] = p
            board.append({"model": key, "name": name, "regime": reg, "regime_name": reg_name,
                          "mae": round(mae(p), 1), "mae_shanghai": round(mae(p, cong), 1),
                          "mae_other": round(mae(p, ~cong), 1),
                          "rmse": round(float(np.sqrt(np.mean((p - y) ** 2))), 1)})
    board.sort(key=lambda r: r["mae"])
    best = board[0]
    pb = preds[(best["model"], best["regime"])]
    p_ridge = preds[("ridge", "real")]

    final = _make_regressor("ridge").fit(X, y)
    coef = final.named_steps["ridge"].coef_ / final.named_steps["standardscaler"].scale_
    names = (["dep_delay", "planned_leg_h", "distance_knm", "buffer_h", "svc_tp8"]
             + [f"to_{p}" for p in ports] + [f"from_{p}" for p in froms])
    points = [{"vessel": l["vessel"], "service": l["service"], "from": l["from"], "to": l["to"],
               "to_name": l["to_name"], "dep_delay": l["dep_delay_h"], "actual": float(y[i]),
               "naive": float(preds_naive[i]), "model": round(float(pb[i]), 1),
               "ridge": round(float(p_ridge[i]), 1)}
              for i, l in enumerate(legs)]
    return {
        "n": int(len(y)), "n_vessels": int(len(np.unique(vessels))), "n_synthetic_legs": int(len(ys)),
        "mae_naive": round(mae(preds_naive), 1), "mae_model": best["mae"],
        "mae_ridge_real": round(mae(p_ridge), 1),
        "best": {k: best[k] for k in ("model", "name", "regime", "regime_name")},
        "mae_naive_shanghai": round(mae(preds_naive, cong), 1),
        "mae_model_shanghai": best["mae_shanghai"],
        "mae_naive_other": round(mae(preds_naive, ~cong), 1),
        "mae_model_other": best["mae_other"],
        "models": board,
        "coefficients": dict(zip(names, [round(float(c), 3) for c in coef])),
        "points": points, "validation": "leave-one-real-vessel-out (tested on real legs only)",
    }
