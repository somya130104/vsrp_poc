"""
app.py — Flask API + static host for the VSRP dashboard (real Maersk TP7/TP8 data).

Precomputed artefacts come from `python build.py` (data/real/*.json).
Live endpoints solve the MILP on demand (≈10–60 ms per solve).
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from src.routes import SERVICES, OUT_DIR
from src.recovery import build_scenario, run_all, pareto, public, scenario_public

app = Flask(__name__, static_folder="dashboard", static_url_path="")
CORS(app)

_cache = {}


def _load(name):
    if name not in _cache:
        path = os.path.join(OUT_DIR, name)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            _cache[name] = json.load(f)
    return _cache[name]


def _advisor():
    if "xgb" not in _cache:
        path = os.path.join(OUT_DIR, "advisor_xgb.json")
        if not os.path.exists(path):
            _cache["xgb"] = None
        else:
            from xgboost import XGBClassifier
            m = XGBClassifier()
            m.load_model(path)
            _cache["xgb"] = m
    return _cache["xgb"]


def _need(name):
    data = _load(name)
    if data is None:
        return None, (jsonify({"error": f"{name} missing — run `python build.py` first"}), 500)
    return data, None


def _f(key, default, lo=None, hi=None):
    try:
        v = float(request.args.get(key, default))
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _scenario_from_args():
    svc = request.args.get("service", "TP8")
    if svc not in SERVICES:
        svc = "TP8"
    rot = SERVICES[svc]["rotation"]
    start = request.args.get("start", rot[3] if svc == "TP8" else rot[2])
    if start not in rot:
        start = rot[0]
    d0 = _f("delay", 150, 0, 500)
    bl = {}
    cong = request.args.get("cong_port", "")
    if cong in rot and cong != start:
        bl[cong] = _f("cong_h", 0, 0, 500)
    params = {
        "bunker_usd_t": _f("bunker", 600, 200, 1500),
        "delay_usd_teu_day": _f("delay_cost", 20, 0, 200),
        "rollover_usd_teu": _f("rollover", 400, 0, 3000),
        "v_max": _f("vmax", 22.0, 14.0, 25.0),
        "compress_max": _f("compress", 0.20, 0.0, 0.30),
    }
    mand = request.args.get("mandatory")
    mandatory = [p for p in mand.split(",") if p in rot] if mand is not None else None
    proforma, _ = _need("proforma.json")
    return build_scenario(proforma, svc, start, d0, bl, params, mandatory=mandatory)


@app.route("/api/overview")
def overview():
    data, err = _need("overview.json")
    return err or jsonify(data)


@app.route("/api/real")
def real():
    data, err = _need("real.json")
    return err or jsonify(data)


@app.route("/api/replays")
def replays():
    data, err = _need("replays.json")
    return err or jsonify(data)


@app.route("/api/ml")
def ml():
    adv, err = _need("advisor.json")
    if err:
        return err
    dm, err = _need("delay_model.json")
    if err:
        return err
    syn = _load("synthetic.json")
    return jsonify({"advisor": {k: v for k, v in adv.items() if k != "empirical"}, "delay_model": dm,
                    "synthetic": syn})


@app.route("/api/optimize")
def optimize():
    if _load("proforma.json") is None:
        return _need("proforma.json")[1]
    scn = _scenario_from_args()
    allow_skip = request.args.get("skip", "1") != "0"
    allow_comp = request.args.get("handling", "1") != "0"
    model = _advisor()
    visits, probs = (None, {})
    if model is not None and allow_skip:
        from src.ml_models import ml_visits
        visits, probs = ml_visits(model, scn)
    plans = run_all(scn, ml_visits=visits, allow_skip=allow_skip, allow_compress=allow_comp)
    ml_probs = [{"node": j, "port": scn["nodes"][j]["port"], "name": scn["nodes"][j]["name"],
                 "p_skip": round(p, 4)} for j, p in sorted(probs.items())]
    return jsonify({"scenario": scenario_public(scn),
                    "plans": {k: public(v) for k, v in plans.items() if v},
                    "ml_probs": ml_probs})


@app.route("/api/pareto")
def pareto_ep():
    if _load("proforma.json") is None:
        return _need("proforma.json")[1]
    scn = _scenario_from_args()
    return jsonify({"points": pareto(scn)})


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "built": _load("overview.json") is not None})


@app.route("/full/")
def full_index():
    return send_from_directory(os.path.join(ROOT, "dashboard_full"), "index.html")


@app.route("/full/<path:path>")
def full_static(path):
    return send_from_directory(os.path.join(ROOT, "dashboard_full"), path)


@app.route("/v2/")
def v2_index():
    return send_from_directory(os.path.join(ROOT, "dashboard_v2"), "index.html")


@app.route("/v2/<path:path>")
def v2_static(path):
    return send_from_directory(os.path.join(ROOT, "dashboard_v2"), path)


@app.route("/")
def index():
    return app.send_static_file("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=False, port=port, use_reloader=False)
