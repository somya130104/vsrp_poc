"""
build.py — run the full real-data pipeline once, before starting the dashboard.

  1. Parse the Maersk TP7/TP8 schedule files
  2. Sea distances + route geometry (searoute, cached)
  3. Replay every real decision point (model vs. Maersk)
  4. Synthetic year: simulate the whole fleet (8 TP8 + 6 TP7 ships, 12 months),
     calibrated on the real data, and check it against the real statistics
  5. ML-1: next-port delay predictor (6 regressors, real vs real+synthetic
           training, always tested on held-out REAL vessels)
  6. ML-2: sample scenarios from real+synthetic distributions, solve with the
           MILP, train 10 classifiers for the recovery advisor, measure regret
  7. Write everything to data/real/ for the Flask API

Usage:  python build.py            (≈5 minutes)
        python build.py --quick    (fewer scenarios, for development)
"""

import json
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.routes import (OUT_DIR, SERVICES, PORTS, PARAMS, PROVENANCE, PAPER_ROUTE,
                        build_distances, distances, DIST_FILE)
from src.real_data import load_all, build_legs, detect_omissions
from src.synthetic import generate as gen_synthetic, validate as validate_synthetic
from src.replay import build_cases
from src.ml_models import train_advisor, train_delay_model


def dump(name, obj):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=float)
    print(f"    wrote data/real/{name} ({os.path.getsize(path) / 1024:.0f} KB)")


def main():
    quick = "--quick" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()

    print("[1/6] Sea distances")
    if not os.path.exists(DIST_FILE) or "--distances" in sys.argv:
        build_distances()
    dist = distances()

    print("[2/6] Parsing Maersk schedule files")
    real = load_all()
    calls = [c for c in real["calls"]]
    tp = [c for c in calls if c["service"] in SERVICES]
    done = [c for c in tp if c["arr_delay_h"] is not None]
    print(f"    {len(calls)} calls, {len(done)} completed on TP7/TP8, {len(real['legs'])} sea legs, "
          f"{len(real['omissions'])} omitted calls")

    fleet = {}
    for c in calls:
        k = (c["vessel"], c["service"])
        f = fleet.setdefault(k, {"vessel": c["vessel"], "imo": c["imo"], "flag": c["flag"],
                                 "service": c["service"], "calls": 0, "completed": 0,
                                 "first": c["pln_arr"], "last": c["pln_arr"], "max_delay": 0.0})
        f["calls"] += 1
        f["completed"] += 1 if c["arr_delay_h"] is not None else 0
        f["last"] = max(f["last"], c["pln_arr"] or f["last"])
        f["first"] = min(f["first"], c["pln_arr"] or f["first"])
        if c["arr_delay_h"] is not None:
            f["max_delay"] = max(f["max_delay"], c["arr_delay_h"])

    # Pacific crossings: planned vs actual
    pac = [l for l in real["legs"] if l["dist_nm"] > 3000 and l["pln_h"]]
    east = [l for l in pac if l["to"] == "USLAX"]
    sha = [c["arr_delay_h"] for c in done if c["port"] == "CNSHA"]

    geometry = {}
    for svc, cfg in SERVICES.items():
        rot = cfg["rotation"]
        geometry[svc] = [{"from": a, "to": rot[(i + 1) % len(rot)],
                          "coords": dist["geometry"][f"{a}-{rot[(i + 1) % len(rot)]}"]}
                         for i, a in enumerate(rot)]
    skip_geom = []
    for o in real["omissions"]:
        skip_geom.append({"service": o["service"], "from": o["from"], "to": o["to"],
                          "coords": dist["geometry"][f"{o['from']}-{o['to']}"]})

    period = sorted(c["act_arr"] for c in done)
    overview = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "services": {s: {**cfg, "proforma": real["proforma"][s]} for s, cfg in SERVICES.items()},
        "ports": PORTS, "params": PARAMS, "provenance": PROVENANCE, "paper_route": PAPER_ROUTE,
        "geometry": geometry, "skip_geometry": skip_geom,
        "fleet": sorted(fleet.values(), key=lambda f: (f["service"], f["vessel"])),
        "omissions": real["omissions"],
        "headline": {
            "n_calls": len(done), "n_vessels": len({c["vessel"] for c in tp}),
            "n_legs": len(real["legs"]), "n_omissions": len(real["omissions"]),
            "period_start": period[0][:10], "period_end": period[-1][:10],
            "shanghai_mean_delay": round(st.mean(sha), 1), "shanghai_max_delay": round(max(sha), 1),
            "shanghai_min_delay": round(min(sha), 1),
            "pacific_east_recovered_mean": round(st.mean(l["recovered_h"] for l in east), 1),
            "pacific_east_recovered_max": round(max(l["recovered_h"] for l in east), 1),
            "pacific_east_speed_mean": round(st.mean(l["avg_speed_kn"] for l in east), 2),
            "pacific_east_pln_speed": round(st.mean(l["pln_speed_kn"] for l in east), 2),
            "max_arr_delay": round(max(c["arr_delay_h"] for c in done), 1),
            "mean_arr_delay": round(st.mean(c["arr_delay_h"] for c in done), 1),
            "on_time_share": round(sum(1 for c in done if c["arr_delay_h"] <= 24) / len(done), 3),
        },
    }
    dump("overview.json", overview)
    dump("real.json", {"calls": calls, "legs": real["legs"], "port_stats": real["port_stats"],
                       "omissions": real["omissions"]})
    dump("proforma.json", real["proforma"])

    print("[3/6] Replaying real decision points")
    cases = build_cases(real["calls"], real["proforma"])
    print(f"    {len(cases)} replay cases")
    dump("replays.json", cases)

    # Default: every model is trained and tested on the REAL Maersk schedules only.
    # `python build.py --synthetic` additionally runs the optional fleet-year simulator.
    syn_legs = None
    if "--synthetic" in sys.argv:
        print("[4/6] Optional: synthetic fleet year (calibrated on real data)")
        syn_calls, syn_info = gen_synthetic(real)
        syn_legs = build_legs(syn_calls)
        syn_omit = detect_omissions(syn_calls)
        val = validate_synthetic(real["calls"], syn_calls, real["omissions"], syn_omit, real["legs"], syn_legs)
        syn_info.update({"n_legs": len(syn_legs), "n_omissions": len(syn_omit), "validation": val})
        print(f"    {syn_info['n_vessels']} simulated vessels, {len(syn_calls)} calls")
        dump("synthetic.json", syn_info)
    else:
        print("[4/6] Real data only (synthetic simulator off)")
        stale = os.path.join(OUT_DIR, "synthetic.json")
        if os.path.exists(stale):
            os.replace(stale, os.path.join(OUT_DIR, "synthetic.unused.json"))

    print("[5/6] ML-1 delay predictor (6 models; tested on held-out real vessels)")
    dm = train_delay_model(real, synthetic_legs=syn_legs)
    for r in dm["models"]:
        print(f"      {r['name']:<24} {r['regime_name']:<17} MAE {r['mae']:>6} h")
    print(f"    MAE naive {dm['mae_naive']} h  ->  best {dm['best']['name']} {dm['mae_model']} h")
    dump("delay_model.json", dm)

    print("[6/6] ML-2 recovery advisor (10 classifiers on MILP-labelled scenarios from real distributions)")
    n = 600 if quick else 3000
    model, summary = train_advisor(real, n=n)
    summary["data"] = {"real_calls": len(done), "real_vessels": overview["headline"]["n_vessels"],
                       "source": "real Maersk TP7/TP8 schedules"}
    model.save_model(os.path.join(OUT_DIR, "advisor_xgb.json"))
    x = summary["xgboost"]
    print(f"    best by regret: {summary['best_model']}")
    print(f"    XGBoost accuracy {x['accuracy']:.3f}  F1 {x['f1']:.3f}  AUC {x['roc_auc']:.3f}  "
          f"mean regret {summary['regret']['mean_pct']}%  (rule {summary['regret']['rule_mean_pct']}%)")
    dump("advisor.json", summary)

    print(f"\nDone in {time.time() - t0:.0f}s. Start the dashboard with:  python app.py")


if __name__ == "__main__":
    main()
