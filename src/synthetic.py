"""
synthetic.py
============
Synthetic voyage generator, calibrated on the real Maersk TP7/TP8 schedules.

Why: the real files cover only 6 vessels and ~5 months (58 completed calls).
TP8 runs ~8 ships and TP7 ~6, so we simulate ONE FULL YEAR for the WHOLE
FLEET (14 vessels) and use it to train/test the learning models. The real
data is never modified; every synthetic row carries "synthetic": True.

How each vessel moves (all ingredients are fitted on the real data):

  1. Planned timetable   – the real proforma (median planned stays / legs),
                           vessels spaced evenly round the loop (weekly headway).
  2. Berth backlog W_p(t) – one shared process per port (so ships at the same
                           port in the same weeks are delayed together).
                           Two regimes, normal / congested (Markov chain, mean
                           spell 4 weeks). In the congested regime the backlog
                           follows an AR(1) around the real mean "berth late"
                           level. Congestion probability = real share, lower
                           outside the Jun–Oct peak season.
  3. Sea leg             – a late ship wins back a fraction phi of its delay,
                           capped by the leg's slack at full speed; phi is fitted
                           from real legs that were not congested.
  4. Arrival lateness    – max(lateness at sea, berth backlog W_p) + noise.
  5. Port stay           – planned stay × real stay ratio (bootstrapped per port);
                           late ships shorten stays a little.
  6. Omission            – optional ports (not Los Angeles) are skipped with a
                           probability that grows with the departure delay,
                           matched to the 3 real omissions.
"""

import statistics as st
from datetime import datetime, timedelta, timezone

import numpy as np

from .routes import SERVICES, PORTS, PARAMS, dist_nm

START = datetime(2025, 11, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 31, tzinfo=timezone.utc)
PEAK_MONTHS = {6, 7, 8, 9, 10}      # the real data window (Jun–Oct) is peak season
OFFPEAK_FACTOR = 0.55               # congestion is less likely outside peak season
SPELL_DAYS = 28                     # mean length of a congestion spell
V_MAX = 20.0                        # kn, realistic upper speed for recovery
MAX_CONG_PROB = 0.90                # never assume a port is congested all year


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%MZ")


# ─────────────────────────────────────────────────────────────────────────────
# CALIBRATION  (everything the simulator needs, measured on real data)
# ─────────────────────────────────────────────────────────────────────────────
def calibrate(real):
    calls, legs = real["calls"], real["legs"]

    # berth backlog per port: same definition as ml_models.empirical
    backlog = {}
    for lg in legs:
        added = (lg["arr_delay_h"] or 0) - max(0.0, lg["dep_delay_h"] or 0)
        cong = lg["est_wait_h"] > 12 and added > 12 and (lg["arr_delay_h"] or 0) > 12
        backlog.setdefault(lg["to"], []).append(lg["arr_delay_h"] if cong else 0.0)
    ports = {}
    for p in PORTS:
        obs = backlog.get(p, [])
        pos = [v for v in obs if v > 0]
        prob = min(MAX_CONG_PROB, len(pos) / len(obs)) if obs else 0.1
        ports[p] = {
            "n_obs": len(obs), "p_cong": round(prob, 3),
            "level_mean": round(st.mean(pos), 1) if pos else 40.0,
            "level_sd": round(st.pstdev(pos), 1) if len(pos) > 1 else 20.0,
        }

    # recovery fraction phi on non-congested legs of late ships
    phis, resid = [], []
    for lg in legs:
        dep = lg["dep_delay_h"] or 0
        if lg["pln_h"] is None or dep < 24:
            continue
        slack = max(1.0, lg["pln_h"] - lg["dist_nm"] / V_MAX - 4)
        arr = lg["arr_delay_h"] or 0
        if arr - dep > 12:            # delay grew: congested, not a recovery leg
            continue
        rec = dep - max(arr, 0)
        phis.append(float(np.clip(rec / min(dep, slack), 0, 1)))
    phi = st.median(phis) if phis else 0.5
    # westbound Pacific (USA -> Asia): Maersk re-plans the long return leg, so a
    # late ship wins back a share of its WHOLE delay, not just the speed slack
    west = [float(np.clip((lg["dep_delay_h"] - max(lg["arr_delay_h"], 0)) / lg["dep_delay_h"], 0, 1))
            for lg in legs if lg["dist_nm"] > 3000 and PORTS[lg["to"]]["lon"] > 0
            and (lg["dep_delay_h"] or 0) > 24 and lg["arr_delay_h"] is not None]
    phi_west = st.median(west) if west else phi
    for lg in legs:
        dep, arr = lg["dep_delay_h"], lg["arr_delay_h"]
        if dep is None or arr is None or lg["pln_h"] is None or dep >= 24:
            continue
        if arr - max(dep, 0) < 12:
            resid.append(arr - max(dep, 0))
    sigma = float(np.std(resid)) if len(resid) > 2 else 10.0

    # stay ratio per port (actual / planned)
    stay = {}
    for c in calls:
        if c["service"] in SERVICES and c["stay_act_h"] and c["stay_pln_h"]:
            stay.setdefault(c["port"], []).append(c["stay_act_h"] / c["stay_pln_h"])
    stay = {p: [round(r, 3) for r in v] for p, v in stay.items()}

    # initial departure delay pool
    dep_pool = {s: [c["dep_delay_h"] for c in calls if c["service"] == s and c["dep_delay_h"] is not None]
                for s in SERVICES}
    return {"ports": ports, "phi": round(phi, 3), "phi_n": len(phis),
            "phi_west": round(phi_west, 3), "phi_west_n": len(west), "sigma_h": round(sigma, 1),
            "stay_ratio": stay, "dep_pool": dep_pool,
            "skip_base": 0.15, "skip_mid_h": 90.0, "skip_scale_h": 25.0}


# ─────────────────────────────────────────────────────────────────────────────
# SHARED BERTH-BACKLOG PROCESS  (one per port, daily resolution)
# ─────────────────────────────────────────────────────────────────────────────
def backlog_paths(cal, rng):
    days = (END - START).days + 120
    out = {}
    for p, c in cal["ports"].items():
        state, level = rng.random() < c["p_cong"], c["level_mean"]
        path = np.zeros(days)
        for d in range(days):
            month = (START + timedelta(days=d)).month
            pi = c["p_cong"] * (1.0 if month in PEAK_MONTHS else OFFPEAK_FACTOR)
            pi = min(pi, 0.95)
            leave = 1.0 / SPELL_DAYS
            enter = leave * pi / max(1e-6, 1 - pi)
            if state and rng.random() < leave:
                state = False
            elif not state and rng.random() < enter:
                state, level = True, max(12.0, rng.normal(c["level_mean"], c["level_sd"] * 0.5))
            if state:
                level = c["level_mean"] + 0.93 * (level - c["level_mean"]) + rng.normal(0, 0.3 * c["level_sd"])
                path[d] = max(0.0, level)
            else:
                path[d] = 0.0
        out[p] = path
    return out


# ─────────────────────────────────────────────────────────────────────────────
# VESSEL SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
def _sim_vessel(name, imo, svc, phase_h, proforma, cal, paths, rng):
    rot = SERVICES[svc]["rotation"]
    pf = {p["port"]: p for p in proforma[svc]["ports"]}
    mandatory = set(SERVICES[svc]["mandatory"])
    n = len(rot)

    # place the vessel at `phase_h` into the loop
    cum, t = [], 0.0
    for p in rot:
        cum.append(t)
        t += pf[p]["stay_h"] + pf[p]["transit_h"]
    loop = t
    k = next((i for i, c in enumerate(cum) if c >= phase_h), 0)
    t_pln = START + timedelta(hours=(cum[k] - phase_h) % loop)

    pool = [d for d in cal["dep_pool"][svc] if d is not None] or [0.0]
    dep_delay = float(max(0.0, pool[rng.integers(len(pool))]))
    calls, i, voy, seq, prev_skipped = [], k, 500 + int(rng.integers(0, 40)), 0, False
    first = True
    while t_pln < END:
        p = rot[i]
        stay_pln = pf[p]["stay_h"]
        day = int((t_pln - START).total_seconds() // 86400)
        W = float(paths[p][min(max(day, 0), len(paths[p]) - 1)])

        # 6. omission decision (decided on the inbound leg)
        if not first and p not in mandatory and not prev_skipped:
            ps = cal["skip_base"] / (1 + np.exp(-(dep_delay - cal["skip_mid_h"]) / cal["skip_scale_h"]))
            if W > 120:
                ps = min(0.6, ps * 2.0)
            if rng.random() < ps:
                prev_skipped = True
                nxt = rot[(i + 1) % n]
                # the skipped port's planned stay + onward leg are saved, minus the
                # extra distance of sailing straight on to the next port
                extra = max(0.0, dist_nm(prev_port, nxt) - dist_nm(prev_port, p)) / 16.0
                dep_delay -= max(0.0, stay_pln + pf[p]["transit_h"] - extra)
                t_pln += timedelta(hours=stay_pln + pf[p]["transit_h"])
                i = (i + 1) % n
                if i == 0:
                    voy += 1
                continue
        prev_skipped = False

        # 3–4. sea leg recovery and arrival
        if first:
            arr_delay = dep_delay
        else:
            leg_pln = pf[prev_port]["transit_h"]
            slack = max(0.0, leg_pln - dist_nm(prev_port, p) / V_MAX - 4)
            late = max(0.0, dep_delay)
            if dist_nm(prev_port, p) > 3000 and PORTS[p]["lon"] > 0:      # westbound Pacific
                phi = float(np.clip(rng.normal(cal["phi_west"], 0.12), 0, 1))
                at_sea = late * (1 - phi) + rng.normal(-2.0, cal["sigma_h"])
            else:
                phi = float(np.clip(rng.normal(cal["phi"], 0.12), 0, 1))
                at_sea = late - phi * min(late, slack) + rng.normal(-2.0, cal["sigma_h"])
            arr_delay = max(at_sea, W * float(np.clip(rng.normal(1.0, 0.1), 0.7, 1.3)))
            arr_delay = max(arr_delay, -24.0)
        first = False

        # 5. port stay
        ratios = cal["stay_ratio"].get(p) or [1.0]
        r = ratios[rng.integers(len(ratios))] * float(np.clip(rng.normal(1.0, 0.06), 0.8, 1.2))
        if arr_delay > 48:
            r *= 0.92
        r = float(np.clip(r, 0.5, 1.8))
        stay_act = stay_pln * r
        dep_delay = arr_delay + stay_act - stay_pln

        pa = t_pln
        pd_ = pa + timedelta(hours=stay_pln)
        aa = pa + timedelta(hours=arr_delay)
        ad = aa + timedelta(hours=stay_act)
        h = lambda dt: dt.timestamp() / 3600.0
        if ad <= END:
            calls.append({
                "service": svc, "vessel": name, "imo": imo, "flag": "SIM", "seq": seq,
                "ref": f"SIM-{imo}-{seq}", "voyage_in": f"{voy}E", "voyage_out": f"{voy}W",
                "port": p, "port_name": PORTS[p]["name"], "terminal": PORTS[p]["name"],
                "pln_arr": _iso(pa), "est_arr": None, "act_arr": _iso(aa),
                "pln_dep": _iso(pd_), "est_dep": None, "act_dep": _iso(ad),
                "t_pln_arr": h(pa), "t_act_arr": h(aa), "t_pln_dep": h(pd_), "t_act_dep": h(ad),
                "t_est_arr": None, "t_est_dep": None,
                "arr_delay_h": round(arr_delay, 1), "dep_delay_h": round(dep_delay, 1),
                "stay_act_h": round(stay_act, 1), "stay_pln_h": round(stay_pln, 1),
                "status": "completed", "synthetic": True,
            })
            seq += 1
        prev_port = p
        t_pln = pd_ + timedelta(hours=pf[p]["transit_h"])
        i = (i + 1) % n
        if i == 0:
            voy += 1
    return calls


def generate(real, seed=11):
    """Simulate one year for the whole TP8 + TP7 fleet. Returns (calls, info)."""
    rng = np.random.default_rng(seed)
    cal = calibrate(real)
    paths = backlog_paths(cal, rng)
    proforma = real["proforma"]
    calls, fleet = [], []
    for svc, cfg in SERVICES.items():
        nv = cfg["fleet_estimate"]
        loop = proforma[svc]["loop_h"]
        for k in range(nv):
            name = f"SIM {svc}-{k + 1:02d}"
            imo = f"9{int(svc[-1])}{k:05d}"
            vc = _sim_vessel(name, imo, svc, k * loop / nv, proforma, cal, paths, rng)
            calls += vc
            fleet.append({"vessel": name, "service": svc, "calls": len(vc)})
    info = {"calibration": {k: v for k, v in cal.items() if k not in ("dep_pool", "stay_ratio")},
            "fleet": fleet, "period_start": START.date().isoformat(), "period_end": END.date().isoformat(),
            "n_calls": len(calls), "n_vessels": len(fleet), "seed": seed}
    return calls, info


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION: do synthetic statistics look like the real ones?
# ─────────────────────────────────────────────────────────────────────────────
def validate(real_calls, syn_calls, real_omissions, syn_omissions, real_legs, syn_legs):
    from scipy.stats import ks_2samp
    rows = []
    for svc, cfg in SERVICES.items():
        for p in cfg["rotation"]:
            r = [c["arr_delay_h"] for c in real_calls if c["service"] == svc and c["port"] == p
                 and c["arr_delay_h"] is not None]
            s = [c["arr_delay_h"] for c in syn_calls if c["service"] == svc and c["port"] == p]
            peak = [c["arr_delay_h"] for c in syn_calls if c["service"] == svc and c["port"] == p
                    and int(c["pln_arr"][5:7]) in PEAK_MONTHS]
            if not r or not s:
                continue
            ks = ks_2samp(r, peak) if len(peak) > 2 else None
            rows.append({
                "service": svc, "port": p, "name": PORTS[p]["name"],
                "n_real": len(r), "n_syn": len(s),
                "mean_real": round(st.mean(r), 1), "mean_syn": round(st.mean(s), 1),
                "mean_syn_peak": round(st.mean(peak), 1) if peak else None,
                "median_real": round(st.median(r), 1), "median_syn": round(st.median(s), 1),
                "ks_p_peak": round(float(ks.pvalue), 3) if ks else None,
            })
    def opp(calls):
        return sum(1 for c in calls if c["service"] in SERVICES)
    pac = lambda legs: [l["recovered_h"] for l in legs if l["dist_nm"] > 3000 and l["to"] == "USLAX"
                        and l["recovered_h"] is not None and (l["dep_delay_h"] or 0) > 48]
    return {
        "ports": rows,
        "omission_rate_real": round(len(real_omissions) / max(1, opp(real_calls)), 3),
        "omission_rate_syn": round(len(syn_omissions) / max(1, len(syn_calls)), 3),
        "pacific_recovered_real": round(st.mean(pac(real_legs)), 1) if pac(real_legs) else None,
        "pacific_recovered_syn": round(st.mean(pac(syn_legs)), 1) if pac(syn_legs) else None,
    }
