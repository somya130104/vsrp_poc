"""
vsrp_optimizer.py
=================
Implements the Vessel Schedule Recovery Problem optimization.

Based on:
  Brouer, Dirksen, Pisinger, Plum & Vaaben (2013)
  "The Vessel Schedule Recovery Problem (VSRP): A MIP model for handling
   disruptions in liner shipping"
  European Journal of Operational Research, 224(2), 362-374.

Mathematical formulation:

Objective (minimise total recovery cost):

    min  Σ_legs  FC(v_leg) · bunker_price          [fuel cost, cubic in speed]
       + Σ_ports c_berth · (1 - x_skip[p])         [port handling fees]
       + π · D_residual                             [delay penalty $/hr]
       + Σ_p x_skip[p] · ω_p                       [cargo rollover/skip penalty]

Where:
  FC(v) = δ · sqrt(10·TEU) · v² · dist · bunker_price  (from Kowatari Eq. 10)
  D_residual = max(0, disruption_delay - time_recovered)
  time_recovered = Σ_legs [dist/v_default - dist/v_opt] + Σ_skip [T_berth]

Method: scipy.optimize.minimize (SLSQP) for continuous speed variables,
        + exhaustive evaluation of port-skip combinations (up to 2 skips).
"""

import json
import math
import numpy as np
from scipy.optimize import minimize
from itertools import combinations
from typing import List, Dict

# ─────────────────────────────────────────────────────────────────────────────
# LOAD METADATA
# ─────────────────────────────────────────────────────────────────────────────
try:
    with open("data/route_metadata.json") as f:
        META = json.load(f)
except FileNotFoundError:
    META = {}


def _get(key, default):
    return META.get(key, default)


PORTS         = _get("ports", [{"name": f"Port_{i}", "code": f"P{i}"} for i in range(7)])
LEG_DISTANCES = _get("leg_distances", [1094, 97, 734, 5445, 21, 386, 4536])
N_PORTS       = len(PORTS)
N_LEGS        = len(LEG_DISTANCES)
TEU           = _get("teu_capacity", 14000)
V_DEFAULT     = _get("v_default", 18.3)
V_MIN         = _get("v_min", 10.0)
V_MAX         = _get("v_max", 25.0)
T_BERTH       = _get("t_berth_base", 42.0)
PORT_FEE      = _get("port_fee_usd", 45000.0)
DELAY_PENALTY = _get("delay_penalty_usd_hr", 2000.0)
SKIP_PENALTY  = _get("skip_penalty_usd", 180000.0)
BUNKER_PRICE  = _get("bunker_price_usd_mt", 650.0)
DELTA         = _get("delta", 5.813e-5)


# ─────────────────────────────────────────────────────────────────────────────
# CORE FORMULAS
# ─────────────────────────────────────────────────────────────────────────────
def fuel_cost_usd(speed: float, dist_nm: float) -> float:
    """
    Fuel cost on one leg (USD).
    FC = δ · sqrt(10·TEU) · v³ · τ · bunker_price
    Substituting τ = dist/v:  FC = δ · sqrt(10·TEU) · v² · dist · bunker_price
    """
    speed = max(V_MIN, min(V_MAX, speed))
    return DELTA * math.sqrt(10 * TEU) * (speed ** 2) * dist_nm * BUNKER_PRICE


def travel_time(speed: float, dist_nm: float) -> float:
    return dist_nm / max(V_MIN, speed)


def time_at_default(dist_nm: float) -> float:
    return dist_nm / V_DEFAULT


# ─────────────────────────────────────────────────────────────────────────────
# COST FUNCTION FOR A GIVEN SPEED VECTOR + SKIP SET
# ─────────────────────────────────────────────────────────────────────────────
def total_cost(speeds: np.ndarray, skips: List[int], disruption_delay: float) -> Dict:
    """
    Compute total cost of a recovery plan.
    speeds: per-leg speed array (length = N_LEGS)
    skips:  list of port indices to skip (within 1..N_PORTS-2)
    """
    fuel      = sum(fuel_cost_usd(speeds[leg], LEG_DISTANCES[leg]) for leg in range(N_LEGS))
    port_fees = PORT_FEE * (N_PORTS - len(skips))
    skip_cost = SKIP_PENALTY * len(skips)

    time_recovered_speed = sum(
        time_at_default(LEG_DISTANCES[leg]) - travel_time(speeds[leg], LEG_DISTANCES[leg])
        for leg in range(N_LEGS)
    )
    time_recovered_skip  = T_BERTH * len(skips)
    time_recovered       = time_recovered_speed + time_recovered_skip

    residual_delay = max(0.0, disruption_delay - time_recovered)
    delay_cost     = DELAY_PENALTY * residual_delay

    return {
        "fuel"          : fuel,
        "port_fees"     : port_fees,
        "skip_penalty"  : skip_cost,
        "delay"         : delay_cost,
        "total"         : fuel + port_fees + skip_cost + delay_cost,
        "residual_delay": residual_delay,
        "time_recovered": time_recovered,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BASELINE (NO RECOVERY)
# ─────────────────────────────────────────────────────────────────────────────
def baseline_cost(disruption_delay: float) -> Dict:
    speeds = np.full(N_LEGS, V_DEFAULT)
    c = total_cost(speeds, [], disruption_delay)
    # Baseline: no recovery actions → full disruption delay incurred
    c["residual_delay"] = disruption_delay
    c["delay"]          = DELAY_PENALTY * disruption_delay
    c["total"]          = c["fuel"] + c["port_fees"] + c["skip_penalty"] + c["delay"]
    return c


# ─────────────────────────────────────────────────────────────────────────────
# CONTINUOUS SPEED OPTIMIZER  (SLSQP — Eq. from Brouer 2013 speed variables)
# ─────────────────────────────────────────────────────────────────────────────
def optimize_speeds(skips: List[int], disruption_delay: float) -> np.ndarray:
    """
    For a fixed skip set, find per-leg speeds minimising total cost.
    Decision variables: v[0..N_LEGS-1] in [V_MIN, V_MAX]
    Constraint: implicit (time budget — handled in residual_delay calculation)
    """
    def objective(v):
        return total_cost(v, skips, disruption_delay)["total"]

    v0     = np.full(N_LEGS, min(V_DEFAULT * 1.08, V_MAX))  # warm start: ~8% speedup
    bounds = [(V_MIN, V_MAX)] * N_LEGS

    result = minimize(
        objective, v0, method="SLSQP",
        bounds=bounds,
        options={"ftol": 1e-8, "maxiter": 500, "disp": False}
    )
    return result.x if result.success else v0


# ─────────────────────────────────────────────────────────────────────────────
# COMBINATORIAL SKIP EVALUATION
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_skip_combinations(disruption_delay: float) -> Dict:
    """
    Enumerate skip combinations up to 2 ports (Brouer constraint: no consecutive).
    For each skip set, solve speed optimization.
    Returns the globally best plan.
    """
    skippable = list(range(1, N_PORTS - 1))  # can't skip port 0 (home) or last

    best_cost    = float("inf")
    best_speeds  = np.full(N_LEGS, V_DEFAULT)
    best_skips   = []
    best_details = {}

    # Build candidate skip sets: no-skip + single + double (non-consecutive)
    candidates = [[]]
    for r in range(1, min(3, len(skippable) + 1)):
        for combo in combinations(skippable, r):
            # Brouer constraint: no two consecutive ports skipped
            if all(combo[i+1] - combo[i] > 1 for i in range(len(combo) - 1)):
                candidates.append(list(combo))

    for skip_set in candidates:
        v_opt = optimize_speeds(skip_set, disruption_delay)
        c     = total_cost(v_opt, skip_set, disruption_delay)
        if c["total"] < best_cost:
            best_cost    = c["total"]
            best_speeds  = v_opt.copy()
            best_skips   = skip_set
            best_details = c

    return {
        "optimal_speeds": [round(float(v), 2) for v in best_speeds],
        "best_skip_set" : best_skips,
        "details"       : best_details,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SOLVER
# ─────────────────────────────────────────────────────────────────────────────
def run_optimizer(disruption_delay: float = 140.0, verbose: bool = False) -> Dict:
    base = baseline_cost(disruption_delay)
    opt  = evaluate_skip_combinations(disruption_delay)

    opt_details = opt["details"]
    savings_pct = max(0.0, (base["total"] - opt_details["total"]) / base["total"] * 100)

    # Build skip decisions dict keyed by port name
    skip_decisions = {}
    for p in range(1, N_PORTS - 1):
        name = PORTS[p]["name"] if PORTS else f"Port_{p}"
        skip_decisions[name] = 1 if p in opt["best_skip_set"] else 0

    if verbose:
        print(f"Baseline total: ${base['total']:,.0f}")
        print(f"Optimized total: ${opt_details['total']:,.0f}")
        print(f"Savings: {savings_pct:.1f}%")

    return {
        "status"              : "Optimal",
        "optimal_speeds"      : opt["optimal_speeds"],
        "skip_decisions"      : skip_decisions,
        "total_delay_hrs"     : round(opt_details["residual_delay"], 1),
        "n_skips"             : len(opt["best_skip_set"]),
        "time_recovered_hrs"  : round(opt_details["time_recovered"], 1),
        "costs": {
            "baseline": {
                "fuel"        : round(base["fuel"]),
                "port_fees"   : round(base["port_fees"]),
                "delay"       : round(base["delay"]),
                "skip_penalty": 0,
                "total"       : round(base["total"]),
            },
            "optimized": {
                "fuel"        : round(opt_details["fuel"]),
                "port_fees"   : round(opt_details["port_fees"]),
                "delay"       : round(opt_details["delay"]),
                "skip_penalty": round(opt_details["skip_penalty"]),
                "total"       : round(opt_details["total"]),
            },
        },
        "savings_pct"             : round(savings_pct, 1),
        "disruption_delay_input"  : disruption_delay,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    for delay in [48, 96, 140, 168]:
        r = run_optimizer(disruption_delay=float(delay), verbose=True)
        print(f"\n── Disruption delay = {delay} hrs ──")
        print(f"  Skip decisions: {r['skip_decisions']}")
        print(f"  Optimal speeds (kn): {r['optimal_speeds']}")
        print(f"  Residual delay: {r['total_delay_hrs']} hrs  (recovered {r['time_recovered_hrs']}h)")
        print(f"  Baseline cost : ${r['costs']['baseline']['total']:,}")
        print(f"  Optimized cost: ${r['costs']['optimized']['total']:,}")
        print(f"  Savings       : {r['savings_pct']}%")
