# Vessel Schedule Recovery Problem (VSRP): real Maersk TP8 / TP7 data

This is a decision-support dashboard for **vessel schedule recovery** in container liner shipping. It is built on real Maersk vessel schedules (planned, estimated and actual timestamps) for the **TP8** and **TP7** transpacific services. TP7 shares 4 of its 5 ports with THE Alliance FP1 rotation used in the base papers. TP8 shares 3 of 6, and it has the richest data: 5 vessels, Shanghai congestion of up to 396 h, and two real omitted calls.

## What it does

| Layer | What | Data |
|---|---|---|
| Real-data pipeline | Parses every port call, sea leg and omitted call. Works out the planned timetable, schedule buffers and berth waiting | **Real** |
| Exact optimisation | One-vessel MILP over the next loop: speed per leg, port omission, berth handling. Uses a Brouer et al. (2013) sailing network with the cubic fuel curve linearised by perspective tangent cuts. Solved by HiGHS in about 10–60 ms | Real timetable + assumed costs |
| Model vs Maersk | 21 real late departures replayed. The real decision is priced against no recovery, the speed-only MILP and the full MILP (with hindsight on berth availability) | **Real** |
| Synthetic year | One full year (Nov 2025 – Oct 2026) for the whole fleet (8 TP8 + 6 TP7 ships), simulated from the real berth-congestion, speed-recovery, port-stay and omission patterns, and checked port by port against the real data (`data/real/synthetic_calls.csv`) | Synthetic, calibrated on real |
| ML-1 delay predictor | Six regressors (ridge, k-NN, random forest, gradient boosting, XGBoost, MLP) for next-port lateness. Each is trained on real legs only and on real + synthetic legs, and always tested leave-one-real-vessel-out against a naive forecast | Tested on **real** |
| ML-2 recovery advisor | 3,000 scenarios sampled from the real + synthetic delay and congestion patterns, solved by the MILP. Ten classifiers (logistic, naive Bayes, k-NN, decision tree, SVM, MLP, random forest, extra trees, sklearn gradient boosting, XGBoost) learn which calls to omit, and are ranked by regret, the extra cost versus the exact optimum | Simulated from real distributions |

Every input is tagged **Real / Derived / Public / Assumed / Simulated** on the Method section of the dashboard.

## Run

```bash
bash start.sh          # installs deps, runs build.py once (~2 min), serves http://localhost:5050
```

Or step by step:

```bash
pip install -r requirements.txt
python build.py        # writes data/real/*.json  (python build.py --quick for a fast dev build)
python app.py
```

## Layout

```
TP7_TP8 Vessel Data/   raw Maersk schedule files (JSON + XLSX)
src/routes.py          services, ports, parameters + provenance, fuel curve, searoute distances
src/real_data.py       parser: calls, legs, omitted calls, proforma timetable, port statistics
src/recovery.py        scenario builder, cost evaluator, MILP, rule-based heuristic, Pareto sweep
src/milp.py            small modelling layer over scipy.optimize.milp (HiGHS)
src/replay.py          model-vs-Maersk replays of real decision points
src/synthetic.py       synthetic fleet-year generator, calibrated on and validated against the real data
src/ml_models.py       ML-1 delay predictor, ML-2 recovery advisor (scenario sampling, 10 classifiers, regret)
build.py               runs the whole pipeline → data/real/
app.py                 Flask API (+ static dashboard)
dashboard/             HTML / CSS / JS (Chart.js, d3 map, KaTeX)
legacy/                the first synthetic-data POC, kept for reference
```

## API

`/api/overview` · `/api/real` · `/api/replays` · `/api/ml` · `/api/optimize?service=TP8&start=CNSHA&delay=150&cong_port=&cong_h=0&bunker=600&delay_cost=20&rollover=400&skip=1&handling=1&mandatory=USLAX` · `/api/pareto?…`

## Note on the legacy POC

The first POC (`legacy/`) used the Kowatari fuel formula `δ·√(10·TEU)·v³` as tonnes per **hour**. The formula gives tonnes per **day**, so fuel cost there is overstated about 24 times. The new model fixes this.
