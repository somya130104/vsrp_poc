"""
replay.py
=========
"Model vs. Maersk": re-run the optimiser on real decision points.

At every real departure where the vessel was ≥ 24 h behind plan, we build a
scenario from the *real* planned timetable of the next calls and the *real*
berth availability we observed afterwards (hindsight), then compare:

  • what Maersk actually did (real timestamps, real omitted calls)
  • doing nothing (keep planned leg durations)
  • MILP with speed + handling only
  • MILP with full recovery (speed + skip + handling)

All four are priced with the same cost model.
"""

from .routes import SERVICES, PARAMS, dist_nm
from .recovery import (build_scenario, evaluate, plan_baseline, solve_milp,
                       MANOEUVRE_H, public, _round, _delay_rate, _residual_rate, _skip_cost, CO2_PER_T)
from .routes import leg_fuel_t


def _blocks(calls):
    out = {}
    for c in calls:
        if c["service"] in SERVICES:
            out.setdefault((c["vessel"], c["service"]), []).append(c)
    for k in out:
        out[k].sort(key=lambda c: c["t_pln_arr"] or 0)
    return out


def evaluate_actual(scn, rows):
    """Price what really happened. rows: per horizon node, real call dict or None (omitted)."""
    P, nodes, teu = scn["params"], scn["nodes"], scn["teu"]
    N = len(nodes) - 1
    t0 = scn["_t0"]
    timeline = [{"node": 0, "port": nodes[0]["port"], "name": nodes[0]["name"], "visited": True,
                 "pln_arr": nodes[0]["pln_arr"], "pln_dep": 0.0, "arr": None, "berth": None,
                 "dep": scn["d0"], "late": scn["d0"], "speed_in": None}]
    fuel_t = delay_usd = skip_usd = 0.0
    prev_port, prev_dep = nodes[0]["port"], scn["d0"]
    for j in range(1, N + 1):
        nd, r = nodes[j], rows[j - 1]
        if r is None:
            skip_usd += _skip_cost(scn, j)
            timeline.append({"node": j, "port": nd["port"], "name": nd["name"], "visited": False,
                             "pln_arr": nd["pln_arr"], "pln_dep": nd["pln_dep"], "arr": None,
                             "berth": None, "dep": None, "late": None, "speed_in": None})
            continue
        arr = r["t_act_arr"] - t0
        dep = (r["t_act_dep"] - t0) if r["t_act_dep"] else arr + nd["stay"]
        D = dist_nm(prev_port, nd["port"])
        hrs = arr - prev_dep
        v = min(24.0, max(PARAMS["v_min"], D / max(hrs - MANOEUVRE_H, 1.0)))
        f = leg_fuel_t(D, v, teu)
        fuel_t += f
        late = max(0.0, arr - nd["pln_arr"])
        delay_usd += late * _delay_rate(scn, j)
        timeline.append({"node": j, "port": nd["port"], "name": nd["name"], "visited": True,
                         "pln_arr": nd["pln_arr"], "pln_dep": nd["pln_dep"], "arr": arr, "berth": arr,
                         "dep": dep, "late": late, "wait": max(0.0, hrs - D / PARAMS["v_ref_wait"]),
                         "speed_in": round(v, 2), "dist_in": D, "compress": 0.0, "fuel_t": f})
        prev_port, prev_dep = nd["port"], dep
    residual = timeline[-1]["late"] or 0.0
    resid_usd = residual * _residual_rate(scn)
    fuel_usd = fuel_t * P["bunker_usd_t"]
    total = fuel_usd + delay_usd + skip_usd + resid_usd
    return {"label": "Maersk (actual)", "total": total,
            "costs": {"fuel": fuel_usd, "delay": delay_usd, "skip": skip_usd, "handling": 0.0,
                      "residual": resid_usd},
            "fuel_t": fuel_t, "co2_t": fuel_t * CO2_PER_T, "residual_h": residual,
            "lateness_sum_h": sum(t["late"] for t in timeline[1:] if t["late"] is not None),
            "skipped": [nodes[j]["port"] for j in range(1, N + 1) if rows[j - 1] is None],
            "timeline": timeline}


def build_cases(calls, proforma, min_delay=24.0, min_nodes=3):
    cases = []
    for (vessel, svc), blk in _blocks(calls).items():
        rot = SERVICES[svc]["rotation"]
        n = len(rot)
        pf = {p["port"]: p for p in proforma[svc]["ports"]}
        for i, c in enumerate(blk):
            if c["t_act_dep"] is None or c["dep_delay_h"] is None or c["dep_delay_h"] < min_delay:
                continue
            if c["port"] not in rot:
                continue
            # walk forward through actual calls, mapping onto rotation positions
            pos = rot.index(c["port"])
            rows, planned, opens = [], [], []
            t0 = c["t_pln_dep"]
            prev_pln_dep = 0.0
            prev_dep_act = c["t_act_dep"]
            k = i + 1
            while k < len(blk) and len(rows) < n:
                nxt = blk[k]
                if nxt["t_act_arr"] is None or nxt["port"] not in rot:
                    break
                target = rot[(pos + 1) % n]
                if nxt["port"] != target:
                    # omitted call(s) until we reach nxt
                    p = target
                    pa = prev_pln_dep + pf[rot[pos]]["transit_h"]
                    pd_ = pa + pf[p]["stay_h"]
                    rows.append(None); planned.append((p, pa, pd_)); opens.append(None)
                    prev_pln_dep = pd_
                    pos = (pos + 1) % n
                    continue
                pa, pd_ = nxt["t_pln_arr"] - t0, nxt["t_pln_dep"] - t0
                leg_h = nxt["t_act_arr"] - prev_dep_act
                Dd = dist_nm(blk[k - 1]["port"], nxt["port"])
                wait = leg_h - Dd / PARAMS["v_ref_wait"]
                open_rel = (nxt["t_act_arr"] - t0) if wait > 12.0 and nxt["arr_delay_h"] > 12 else None
                rows.append(nxt); planned.append((nxt["port"], pa, pd_)); opens.append(open_rel)
                prev_pln_dep = pd_
                prev_dep_act = nxt["t_act_dep"] if nxt["t_act_dep"] else nxt["t_act_arr"]
                pos = (pos + 1) % n
                k += 1
            # trim trailing omitted nodes
            while rows and rows[-1] is None:
                rows.pop(); planned.pop(); opens.pop()
            if len(rows) < min_nodes:
                continue
            scn = build_scenario(proforma, svc, c["port"], c["dep_delay_h"], horizon=len(rows),
                                 planned=planned, opens=opens)
            scn["_t0"] = t0
            actual = evaluate_actual(scn, rows)
            base = plan_baseline(scn)
            speed = solve_milp(scn, allow_skip=False, label="MILP · speed + handling")
            full = solve_milp(scn, label="MILP · full recovery")
            plans = {"actual": actual, "baseline": base, "speed": speed, "milp": full}
            for p in plans.values():
                p["saving_pct"] = round(100 * (base["total"] - p["total"]) / base["total"], 2) if base["total"] else 0
            case_id = f"{svc}-{vessel.split()[0][:3].upper()}{vessel.split()[-1][:3].upper()}-{c['voyage_out']}-{c['port']}"
            cases.append({
                "id": case_id, "service": svc, "vessel": vessel, "voyage": c["voyage_out"],
                "port": c["port"], "port_name": c["port_name"], "date": c["act_dep"],
                "d0": c["dep_delay_h"],
                "horizon": [p[0] for p in planned],
                "congested": [planned[j][0] for j in range(len(planned)) if opens[j] is not None],
                "real_skips": [planned[j][0] for j in range(len(planned)) if rows[j] is None],
                "plans": {k: public(v) for k, v in plans.items()},
                "scenario": {"nodes": _round(scn["nodes"])},
            })
    cases.sort(key=lambda c: (c["service"], c["date"]))
    return cases
