"""
recovery.py
===========
The Vessel Schedule Recovery Problem for one delayed vessel, following the
sailing-option network idea of Brouer et al. (2013, EJOR).

A *scenario* is: a vessel has just left port 0 of a horizon of N upcoming
calls, D0 hours behind its planned departure. Berths at some ports may only
become available late (congestion). Decide:

  • which upcoming calls to make or omit (port skipping)
  • the sailing speed on every leg actually sailed (discrete options)
  • how many berth hours to buy back with extra handling capacity

minimising   fuel  +  cargo-weighted lateness  +  rollover of skipped cargo
             +  extra handling  +  residual delay carried into the next loop

Four strategies are evaluated with *the same* cost model so they can be
compared fairly:

  baseline   keep the planned leg durations, call every port (do nothing)
  rule       Kowatari & Shibasaki (2025) heuristic: speed ∝ delay, skip on threshold
  speed      MILP, speed + handling only (no skipping)
  milp       MILP, full recovery (speed + skip + handling)
"""

import math
import time

import numpy as np

from .milp import Model
from .routes import SERVICES, PARAMS, PORTS, dist_nm, leg_fuel_t

MANOEUVRE_H = 3.0      # pilotage in/out added to every sailed leg
CO2_PER_T = 3.151      # t CO2 per t VLSFO (IMO factor)
BIG_M = 6000.0


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO
# ─────────────────────────────────────────────────────────────────────────────
def build_scenario(proforma, service, start_port, d0, berth_late=None, params=None,
                   horizon=None, planned=None, opens=None, mandatory=None):
    """
    proforma   : real_data.build_proforma() output
    start_port : port the vessel has just departed (node 0)
    d0         : departure delay at node 0 (hours)
    berth_late : {port: hours} berth becomes available this many hours after plan
    planned    : optional explicit [(port, pln_arr, pln_dep)] for nodes 1..N (replay)
    opens      : optional explicit berth-open times per node (replay, hindsight)
    """
    P = dict(PARAMS)
    if params:
        P.update({k: v for k, v in params.items() if v is not None})
    cfg = SERVICES[service]
    rot = cfg["rotation"]
    pf = {p["port"]: p for p in proforma[service]["ports"]}
    n_h = horizon or len(rot)
    i0 = rot.index(start_port)
    seq = [rot[(i0 + k) % len(rot)] for k in range(n_h + 1)]
    berth_late = berth_late or {}

    nodes = [{"port": seq[0], "pln_arr": -pf[seq[0]]["stay_h"], "pln_dep": 0.0,
              "stay": pf[seq[0]]["stay_h"], "open": None, "mandatory": True}]
    t = 0.0
    for k in range(1, n_h + 1):
        p, prev = seq[k], seq[k - 1]
        if planned:
            pa, pd_ = planned[k - 1][1], planned[k - 1][2]
        else:
            pa = t + pf[prev]["transit_h"]
            pd_ = pa + pf[p]["stay_h"]
        t = pd_
        stay = max(pd_ - pa, 1.0)
        if opens and opens[k - 1] is not None:
            op = opens[k - 1]
        else:
            w = berth_late.get(p, 0.0) if p not in [n["port"] for n in nodes[1:]] else 0.0
            op = pa + w if w > 0 else pa - P["early_berth_h"]
        nodes.append({
            "port": p, "pln_arr": pa, "pln_dep": pd_, "stay": stay, "open": op,
            "teu": pf[p]["teu"],
            "mandatory": (p in (cfg["mandatory"] if mandatory is None else mandatory)) or k == n_h,
            "berth_late": max(0.0, op - pa),
        })
    for k, nd in enumerate(nodes):
        nd["name"] = PORTS[nd["port"]]["name"]
        if k > 0:
            nd["pln_transit"] = nd["pln_arr"] - nodes[k - 1]["pln_dep"]
    loop_teu = sum(pf[p]["teu"] for p in rot)
    return {"service": service, "teu": cfg["teu"], "d0": float(max(0.0, d0)), "nodes": nodes,
            "params": P, "loop_teu": loop_teu}


def _delay_rate(scn, j):
    """$ per hour of lateness at node j (cargo-weighted)."""
    return scn["nodes"][j]["teu"] * scn["params"]["delay_usd_teu_day"] / 24.0


def _residual_rate(scn):
    return scn["params"]["residual_factor"] * scn["loop_teu"] * scn["params"]["delay_usd_teu_day"] / 24.0


def _skip_cost(scn, j):
    nd = scn["nodes"][j]
    return nd["teu"] * scn["params"]["rollover_usd_teu"] - scn["params"]["port_fee_usd"]


def speed_options(P):
    v, out = P["v_min"], []
    while v <= P["v_max"] + 1e-9:
        out.append(round(v, 2))
        v += P["speed_step"]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATOR  (single source of truth for costs)
# ─────────────────────────────────────────────────────────────────────────────
def evaluate(scn, visits, speeds, compress=None, arc_hours=None, label=""):
    """
    visits    : list of node indices actually called, increasing, starting after 0, ending at N
    speeds    : {(i, j): knots}
    compress  : {j: berth hours saved}
    arc_hours : optional {(i, j): hours} to force a leg duration (baseline / replay)
    """
    P, nodes, teu = scn["params"], scn["nodes"], scn["teu"]
    compress = compress or {}
    N = len(nodes) - 1
    d = scn["d0"]
    prev = 0
    timeline = [{"node": 0, "port": nodes[0]["port"], "name": nodes[0]["name"],
                 "visited": True, "pln_arr": nodes[0]["pln_arr"], "pln_dep": 0.0,
                 "arr": None, "berth": None, "dep": d, "late": d, "speed_in": None}]
    fuel_t = fuel_usd = delay_usd = skip_usd = comp_usd = 0.0
    visited = set(visits)
    for j in range(1, N + 1):
        nd = nodes[j]
        if j not in visited:
            skip_usd += _skip_cost(scn, j)
            timeline.append({"node": j, "port": nd["port"], "name": nd["name"], "visited": False,
                             "pln_arr": nd["pln_arr"], "pln_dep": nd["pln_dep"],
                             "arr": None, "berth": None, "dep": None, "late": None, "speed_in": None})
            continue
        dist = dist_nm(nodes[prev]["port"], nd["port"])
        v = speeds.get((prev, j))
        if arc_hours and (prev, j) in arc_hours:
            hrs = arc_hours[(prev, j)]
            v_sail = v if v else max(dist / max(hrs - MANOEUVRE_H, 1.0), P["v_min"])
        else:
            v_sail = v
            hrs = dist / v_sail + MANOEUVRE_H
        f = leg_fuel_t(dist, v_sail, teu)
        fuel_t += f
        a = d + hrs
        b = max(a, nd["open"])
        c = min(compress.get(j, 0.0), P["compress_max"] * nd["stay"])
        dep = b + nd["stay"] - c
        late = max(0.0, b - nd["pln_arr"])
        delay_usd += late * _delay_rate(scn, j)
        comp_usd += c * P["compress_usd_h"]
        timeline.append({"node": j, "port": nd["port"], "name": nd["name"], "visited": True,
                         "pln_arr": nd["pln_arr"], "pln_dep": nd["pln_dep"],
                         "arr": a, "berth": b, "dep": dep, "late": late,
                         "wait": b - a, "speed_in": round(v_sail, 2), "dist_in": dist,
                         "compress": c, "fuel_t": f})
        d, prev = dep, j
    residual = timeline[-1]["late"] or 0.0
    resid_usd = residual * _residual_rate(scn)
    fuel_usd = fuel_t * P["bunker_usd_t"]
    total = fuel_usd + delay_usd + skip_usd + comp_usd + resid_usd
    return {
        "label": label,
        "total": total,
        "costs": {"fuel": fuel_usd, "delay": delay_usd, "skip": skip_usd,
                  "handling": comp_usd, "residual": resid_usd},
        "fuel_t": fuel_t, "co2_t": fuel_t * CO2_PER_T,
        "residual_h": residual,
        "lateness_sum_h": sum(t["late"] for t in timeline[1:] if t["late"] is not None),
        "skipped": [nodes[j]["port"] for j in range(1, N + 1) if j not in visited],
        "timeline": timeline,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGIES
# ─────────────────────────────────────────────────────────────────────────────
def plan_baseline(scn):
    """Do nothing: keep every planned leg duration, call every port."""
    N = len(scn["nodes"]) - 1
    visits = list(range(1, N + 1))
    arc = {(j - 1, j): scn["nodes"][j]["pln_transit"] for j in visits}
    return evaluate(scn, visits, {}, arc_hours=arc, label="No recovery")


def plan_rule(scn, alpha=0.004, t_skip=96.0):
    """Kowatari & Shibasaki (2025) Eq.6–7: v = v_plan·(1+α·D); skip next port if D ≥ T_skip."""
    P, nodes = scn["params"], scn["nodes"]
    N = len(nodes) - 1
    i, d = 0, scn["d0"]
    visits, speeds, consec = [], {}, 0
    while i < N:
        j = i + 1
        late = d - nodes[i]["pln_dep"]
        while (late >= t_skip and not nodes[j]["mandatory"] and consec < P["max_consec_skips"]
               and j < N):
            j += 1
            consec += 1
            late -= nodes[j - 1]["stay"]   # skipping a call recovers roughly its berth time
        if j == i + 1:
            consec = 0
        dist = dist_nm(nodes[i]["port"], nodes[j]["port"])
        v_plan = max(dist / max(nodes[j]["pln_arr"] - nodes[i]["pln_dep"] - MANOEUVRE_H, 1.0), P["v_min"])
        v = min(P["v_max"], max(P["v_min"], v_plan * (1 + alpha * max(d - nodes[i]["pln_dep"], 0))))
        speeds[(i, j)] = v
        a = d + dist / v + MANOEUVRE_H
        b = max(a, nodes[j]["open"])
        d = b + nodes[j]["stay"]
        visits.append(j)
        i = j
    return evaluate(scn, visits, speeds, label="Rule-based (Kowatari 2025)")


def solve_milp(scn, allow_skip=True, allow_compress=True, fixed_visits=None,
               delay_weight=1.0, label="MILP recovery", n_tangents=21):
    """
    Exact MILP over the sailing network (Brouer et al. 2013 structure).

    Binary   y_ij  sail directly from call i to call j (j-i-1 calls omitted)
             x_j   call at port j
    Cont.    T_ij  sailing hours on arc (i, j); speed = dist / T_ij
             F_ij  fuel cost on arc (i, j)
             a_j, b_j, d_j  arrival, berth start, departure;  L_j lateness;  c_j handling

    Fuel is convex in sailing time (F = k·D³/T²).  It is linearised exactly
    at the optimum with perspective tangent cuts  F ≥ 3f_k·y − (2f_k/T_k)·T,
    so no big-M is needed on the fuel term.
    """
    t0 = time.perf_counter()
    P, nodes = scn["params"], scn["nodes"]
    N = len(nodes) - 1
    d0 = scn["d0"]
    K = P["max_consec_skips"] if allow_skip else 0
    m = Model()
    price, teu = P["bunker_usd_t"], scn["teu"]
    coef = P["delta"] * math.sqrt(10.0 * teu) / 24.0 * price  # $·h² per nm³ factor

    # horizon upper bound on any clock time
    U = d0 + max(0.0, max(nd["open"] - d0 for nd in nodes[1:])) + sum(
        nd["stay"] + dist_nm(nodes[j - 1]["port"], nd["port"]) / P["v_min"] + MANOEUVRE_H
        for j, nd in enumerate(nodes) if j > 0) + 50.0
    M = U - d0

    x = {}
    for j in range(1, N + 1):
        x[j] = m.var(f"x{j}", binary=True, cost=-_skip_cost(scn, j))
        if nodes[j]["mandatory"] or not allow_skip:
            m.fix(x[j], 1)
        elif fixed_visits is not None:
            m.fix(x[j], 1 if j in fixed_visits else 0)
    m.const = sum(_skip_cost(scn, j) for j in range(1, N + 1))

    a, b, d, L, c = {}, {}, {}, {}, {}
    d[0] = m.var("d0", lb=d0, ub=d0)
    for j in range(1, N + 1):
        nd = nodes[j]
        lb_b = max(nd["open"], d0)
        a[j] = m.var(f"a{j}", lb=d0, ub=U)
        b[j] = m.var(f"b{j}", lb=lb_b, ub=U)
        d[j] = m.var(f"d{j}", lb=d0, ub=U)
        rate = _delay_rate(scn, j) * delay_weight
        if j == N:
            rate += _residual_rate(scn) * delay_weight
        L[j] = m.var(f"L{j}", lb=0.0, ub=U, cost=rate)
        cmax = P["compress_max"] * nd["stay"] if allow_compress else 0.0
        c[j] = m.var(f"c{j}", lb=0.0, ub=cmax, cost=P["compress_usd_h"])
        m.add({b[j]: 1, a[j]: -1}, lo=0)                              # berth after arrival
        m.add({d[j]: 1, b[j]: -1, c[j]: 1}, lo=nd["stay"])             # berth time
        if cmax > 0:
            m.add({c[j]: 1, x[j]: -cmax}, hi=0)                        # handling only if called
        Mj = max(0.0, lb_b - nd["pln_arr"])
        m.add({L[j]: 1, b[j]: -1, x[j]: -Mj}, lo=-nd["pln_arr"] - Mj)  # lateness if called

    arcs = {}
    for i in range(0, N):
        for j in range(i + 1, min(N, i + 1 + K) + 1):
            D = dist_nm(nodes[i]["port"], nodes[j]["port"])
            tmin, tmax = D / P["v_max"], D / P["v_min"]
            y = m.var(f"y{i}_{j}", binary=True)
            T = m.var(f"T{i}_{j}", lb=0.0, ub=tmax)
            F = m.var(f"F{i}_{j}", lb=0.0, cost=1.0)
            m.add({T: 1, y: -tmin}, lo=0)
            m.add({T: 1, y: -tmax}, hi=0)
            for k in range(n_tangents):
                vk = P["v_min"] + (P["v_max"] - P["v_min"]) * k / (n_tangents - 1)
                Tk = D / vk
                fk = coef * D ** 3 / Tk ** 2
                m.add({F: 1, y: -3 * fk, T: 2 * fk / Tk}, lo=0)
            # a_j >= d_i + T + manoeuvre   when y = 1
            m.add({a[j]: 1, d[i]: -1, T: -1, y: -(MANOEUVRE_H + M)}, lo=-M)
            arcs[(i, j)] = (y, T, D)

    m.add({arcs[k][0]: 1 for k in arcs if k[0] == 0}, lo=1, hi=1)
    for j in range(1, N + 1):
        inflow = {arcs[k][0]: 1 for k in arcs if k[1] == j}
        inflow[x[j]] = -1
        m.add(inflow, lo=0, hi=0)
        if j < N:
            out = {arcs[k][0]: 1 for k in arcs if k[0] == j}
            out[x[j]] = -1
            m.add(out, lo=0, hi=0)

    res = m.solve()
    ms = (time.perf_counter() - t0) * 1000
    if res.x is None:
        return None
    xs = res.x
    visits = [j for j in range(1, N + 1) if xs[x[j]] > 0.5]
    speeds = {}
    for (i, j), (y, T, D) in arcs.items():
        if xs[y] > 0.5:
            speeds[(i, j)] = float(min(P["v_max"], max(P["v_min"], D / max(xs[T], 1e-6))))
    compress = {j: float(xs[c[j]]) for j in range(1, N + 1) if xs[c[j]] > 1e-6}
    out = evaluate(scn, visits, speeds, compress, label=label)
    out["solve_ms"] = round(ms, 1)
    out["n_vars"] = len(m.names)
    out["n_binary"] = int(sum(m.integ))
    out["n_cons"] = len(m.rows)
    out["objective_model"] = float(res.fun + m.const)
    return out


def run_all(scn, ml_visits=None, allow_skip=True, allow_compress=True):
    base = plan_baseline(scn)
    plans = {
        "baseline": base,
        "rule": plan_rule(scn),
        "speed": solve_milp(scn, allow_skip=False, allow_compress=allow_compress,
                            label="MILP · speed + handling"),
        "milp": solve_milp(scn, allow_skip=allow_skip, allow_compress=allow_compress,
                           label="MILP · full recovery"),
    }
    if ml_visits is not None:
        plans["ml"] = solve_milp(scn, fixed_visits=ml_visits, allow_compress=allow_compress,
                                 label="ML-guided MILP")
    for k, p in plans.items():
        if p:
            p["saving_pct"] = round(100 * (base["total"] - p["total"]) / base["total"], 2) if base["total"] > 0 else 0.0
    return plans


def pareto(scn, weights=None):
    """Fuel vs. lateness frontier by re-weighting the delay term."""
    weights = weights or [0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0]
    pts = []
    for w in weights:
        p = solve_milp(scn, delay_weight=w)
        if p:
            pts.append({"weight": w, "fuel_t": round(p["fuel_t"], 1),
                        "fuel_usd": round(p["costs"]["fuel"]),
                        "lateness_h": round(p["lateness_sum_h"], 1),
                        "residual_h": round(p["residual_h"], 1),
                        "skipped": p["skipped"]})
    return pts


# ─────────────────────────────────────────────────────────────────────────────
# SERIALISATION
# ─────────────────────────────────────────────────────────────────────────────
def _round(o):
    if isinstance(o, float):
        return round(o, 2) if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _round(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_round(v) for v in o]
    if isinstance(o, (np.floating,)):
        return round(float(o), 2)
    return o


def public(plan):
    if plan is None:
        return None
    return _round({k: v for k, v in plan.items()})


def scenario_public(scn):
    return _round({"service": scn["service"], "d0": scn["d0"], "teu": scn["teu"],
                   "nodes": scn["nodes"], "params": scn["params"]})
