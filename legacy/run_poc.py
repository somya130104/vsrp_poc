"""
run_poc.py
==========
Master setup script. Run this ONCE before launching the dashboard.
It:
  1. Generates all synthetic data
  2. Runs the MILP optimizer across disruption levels
  3. Runs the Pareto sweep simulation
  4. Trains the XGBoost classifier
  5. Saves all JSON output for the dashboard
  6. Starts the Flask server and opens the browser
"""

import sys, os, json, subprocess, webbrowser, time

# Make sure src/ is importable
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("  VSRP POC — Vessel Schedule Recovery Problem")
print("  Based on Brouer 2013 (EJOR) + Kowatari 2025 (IEEE BigData)")
print("=" * 60)

# ── Step 1: Generate data ──────────────────────────────────────────────────
print("\n[1/5] Generating synthetic liner service data...")
from src.data_generator import save_all
baseline, disrupted, ml_data = save_all()

# ── Step 2: Run optimizer across disruption levels ─────────────────────────
print("\n[2/5] Running MILP optimizer (Brouer 2013 formulation)...")
from src.vsrp_optimizer import run_optimizer
optimizer_results = {}
for delay in [48, 96, 140, 168]:
    r = run_optimizer(disruption_delay=float(delay))
    optimizer_results[str(delay)] = r
    pct = r["savings_pct"]
    print(f"  Delay={delay}h → savings={pct}% | skips={r['n_skips']} | status={r['status']}")

with open("data/optimizer_results.json", "w") as f:
    json.dump(optimizer_results, f, indent=2)
print("✓ Optimizer results saved")

# ── Step 3: Pareto sweep simulation ────────────────────────────────────────
print("\n[3/5] Running multi-agent simulation (Pareto sweep)...")
from src.simulation_agent import run_pareto_sweep, run_skip_threshold_sweep

pareto = run_pareto_sweep(horizon=1500)
with open("data/pareto_results.json", "w") as f:
    json.dump(pareto, f, indent=2)

skip_sweep = run_skip_threshold_sweep(horizon=1500)
with open("data/skip_threshold_results.json", "w") as f:
    json.dump(skip_sweep, f, indent=2)
print("✓ Simulation results saved")

# ── Step 4: Train ML classifier ────────────────────────────────────────────
print("\n[4/5] Training XGBoost skip classifier...")
from src.ml_classifier import run_and_save
model, ml_results = run_and_save()

# ── Step 5: Summary report ─────────────────────────────────────────────────
print("\n[5/5] POC Summary Report")
print("─" * 50)
r140 = optimizer_results.get("140", {})
print(f"  Route      : Yokohama → Shanghai → Ningbo → Yantian → LA → LB → Oakland → Yokohama")
print(f"  Vessels    : 10  |  TEU: 14,000  |  Weekly service")
print(f"  Disruption : LA/LB port congestion (+140h anchorage)")
print(f"  MILP result: {r140.get('savings_pct', 'N/A')}% cost reduction vs no-recovery baseline")
print(f"  Skips      : {r140.get('n_skips', 'N/A')} ports skipped in optimal recovery")
print(f"  ML accuracy: {ml_results['metrics']['accuracy']:.1%} skip prediction")
print(f"  Pareto pts : {len(pareto)} scenarios computed")
print("─" * 50)

print("\n✓ All data ready! Starting dashboard server...")
print("  Opening http://localhost:5050 in your browser...\n")

# Open browser after brief delay
def open_browser():
    time.sleep(2)
    webbrowser.open("http://localhost:5050")

import threading
t = threading.Thread(target=open_browser, daemon=True)
t.start()

# Start Flask
from app import app
app.run(debug=False, port=5050, use_reloader=False)
