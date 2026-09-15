"""
app.py  —  Flask API backend for the VSRP Dashboard
Serves JSON results from the optimizer, simulation, and ML classifier.
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, request
from flask_cors import CORS

# ── Lazy imports of heavy modules ──────────────────────────────────────────
_model_cache  = {}   # stores trained XGBoost model in memory
_pareto_cache = None
_ml_results   = None

app = Flask(__name__, static_folder='dashboard', static_url_path='')
CORS(app)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/route_meta")
def route_meta():
    data = _load_json("data/route_metadata.json")
    if not data:
        return jsonify({"error": "metadata not found — run run_poc.py first"}), 500
    return jsonify(data)


@app.route("/api/optimize")
def optimize():
    """Run MILP optimizer for given disruption delay."""
    delay = float(request.args.get("delay", 140))
    delay = max(24, min(200, delay))

    from src.vsrp_optimizer import run_optimizer
    result = run_optimizer(disruption_delay=delay)
    return jsonify(result)


@app.route("/api/pareto")
def pareto():
    """Return Pareto sweep results (cached after first call)."""
    global _pareto_cache
    if _pareto_cache is None:
        cached = _load_json("data/pareto_results.json")
        if cached:
            _pareto_cache = cached
        else:
            from src.simulation_agent import run_pareto_sweep
            _pareto_cache = run_pareto_sweep(horizon=1500)
            with open("data/pareto_results.json", "w") as f:
                json.dump(_pareto_cache, f)
    return jsonify(_pareto_cache)


@app.route("/api/skip_threshold")
def skip_threshold():
    """Return skip threshold sweep results."""
    cached = _load_json("data/skip_threshold_results.json")
    if cached:
        return jsonify(cached)
    from src.simulation_agent import run_skip_threshold_sweep
    results = run_skip_threshold_sweep(horizon=1500)
    with open("data/skip_threshold_results.json", "w") as f:
        json.dump(results, f)
    return jsonify(results)


@app.route("/api/ml_results")
def ml_results():
    """Return ML classifier metrics + feature importances."""
    data = _load_json("data/ml_results.json")
    if not data:
        return jsonify({"error": "ML results not found — run run_poc.py first"}), 500
    return jsonify(data)


@app.route("/api/predict_skip", methods=["GET"])
def predict_skip():
    """Real-time skip probability from slider inputs."""
    global _model_cache
    if "model" not in _model_cache:
        try:
            from src.ml_classifier import load_data, train_classifier
            df = load_data()
            model, _, _ = train_classifier(df)
            _model_cache["model"] = model
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    model = _model_cache["model"]
    features = {
        "delay_hrs"           : float(request.args.get("delay",    80)),
        "queue_length"        : float(request.args.get("queue",     4)),
        "vessel_teu"          : float(request.args.get("teu",   14000)),
        "leg_dist_nm"         : float(request.args.get("dist",    386)),
        "hist_skip_rate"      : float(request.args.get("skip_hist", 0.15)),
        "handling_efficiency" : float(request.args.get("h_eff",    0.6)),
        "fuel_price_usd_mt"   : float(request.args.get("fuel_p",  650)),
        "days_since_disruption": float(request.args.get("days",    20)),
    }

    from src.ml_classifier import predict_skip_probability
    prob = predict_skip_probability(model, features)
    return jsonify({"skip_probability": round(prob, 4), "features": features})


@app.route("/api/simulation_baseline")
def simulation_baseline():
    """Run a single baseline simulation and return time series."""
    from src.simulation_agent import VSRPSimulation
    sim = VSRPSimulation(
        alpha=0.03, beta=0.01, t_skip=72,
        disruption_start=1000, horizon=2000
    )
    result = sim.run()
    return jsonify({
        "delay_series": result["delay_series"],
        "fuel_series" : result["fuel_series"],
        "avg_delay"   : result["avg_delay_hrs"],
        "avg_fuel"    : result["avg_fuel_ton_hr"],
        "total_skips" : result["total_skips"],
        "skip_events" : result["skip_events"],
        "peak_queue"  : result["peak_queue_la"],
    })


@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/<path:path>")
def static_files(path):
    return app.send_static_file(path)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    port = int(os.environ.get('PORT', 5050))
    app.run(debug=False, port=port, use_reloader=False)
