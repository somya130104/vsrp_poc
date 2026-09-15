#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# VSRP POC — One-click launch script
# Usage: bash start.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

cd "$(dirname "$0")"

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  VSRP POC — Vessel Schedule Recovery Problem Dashboard    ║"
echo "║  Brouer 2013 (EJOR) + Kowatari 2025 (IEEE BigData)       ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.9+"
    exit 1
fi

# Install dependencies if needed
if ! python3 -c "import xgboost" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install pulp numpy pandas scikit-learn xgboost flask flask-cors scipy
fi

# Check if data already exists
if [ ! -f "data/route_metadata.json" ]; then
    echo "Generating data..."
    python3 -c "
import sys; sys.path.insert(0, '.')
from src.data_generator import save_all
save_all()
"
fi

if [ ! -f "data/optimizer_results.json" ]; then
    echo "Pre-computing optimizer results..."
    python3 -c "
import sys, json; sys.path.insert(0, '.')
from src.vsrp_optimizer import run_optimizer
results = {}
for d in [48, 96, 140, 168]:
    results[str(d)] = run_optimizer(float(d))
    print(f'  delay={d}h: {results[str(d)][\"savings_pct\"]}% savings')
with open('data/optimizer_results.json','w') as f: json.dump(results, f, indent=2)
"
fi

if [ ! -f "data/pareto_results.json" ]; then
    echo "Running simulation sweep (takes ~30s)..."
    python3 -c "
import sys, json; sys.path.insert(0, '.')
from src.simulation_agent import run_pareto_sweep, run_skip_threshold_sweep
pareto = run_pareto_sweep(horizon=1200)
with open('data/pareto_results.json','w') as f: json.dump(pareto, f)
sweep = run_skip_threshold_sweep(horizon=1200)
with open('data/skip_threshold_results.json','w') as f: json.dump(sweep, f)
print(f'  {len(pareto)} Pareto points computed')
"
fi

if [ ! -f "data/ml_results.json" ]; then
    echo "Training ML classifier..."
    python3 -c "
import sys; sys.path.insert(0, '.')
from src.ml_classifier import run_and_save
model, res = run_and_save()
print(f'  Accuracy: {res[\"metrics\"][\"accuracy\"]:.1%}')
"
fi

echo ""
echo "✓ All models ready!"
echo ""
echo "Starting dashboard at: http://localhost:5050"
echo "(Press Ctrl+C to stop)"
echo ""

# Open browser
if command -v open &>/dev/null; then
    sleep 2 && open "http://localhost:5050" &
fi

# Start Flask
python3 app.py
