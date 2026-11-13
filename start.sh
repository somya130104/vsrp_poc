#!/bin/bash
# VSRP — one-click launch: install deps, build real-data artefacts once, start dashboard.
set -e
cd "$(dirname "$0")"

PY=python3
[ -x venv/bin/python ] && PY=venv/bin/python

if ! $PY -c "import xgboost, scipy, flask, flask_cors, sklearn" 2>/dev/null; then
  echo "Installing dependencies..."
  $PY -m pip install -r requirements.txt
fi

if [ ! -f data/real/overview.json ] || [ ! -f data/real/advisor_xgb.json ]; then
  echo "Building real-data artefacts (about 2 minutes)..."
  $PY build.py
fi

echo ""
echo "Dashboard: http://localhost:5050   (Ctrl+C to stop)"
if command -v open &>/dev/null; then (sleep 2 && open "http://localhost:5050") & fi
$PY app.py
