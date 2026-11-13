"""
real_data.py
============
Parses the Maersk DCSA-style vessel schedule files (TP7 / TP8) into clean
tables used by every other module:

  calls      one row per port call (planned / estimated / actual times, delays)
  legs       one row per sea leg between two consecutive *actual* calls
  omissions  port calls that were in the rotation but never happened (skips)
  proforma   the planned timetable of each service (median planned stays / legs)
"""

import glob
import json
import os
import statistics as st
from datetime import datetime, timezone

from .routes import RAW_DIR, SERVICES, PORTS, PARAMS, dist_nm


def _hours(dt):
    return dt.timestamp() / 3600.0 if dt else None


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if dt else None


def _r(x, n=1):
    return None if x is None else round(x, n)


def load_calls(raw_dir=RAW_DIR):
    """Return a list of call dicts for TP7/TP8 (other services kept, flagged)."""
    calls, seen = [], set()
    for path in sorted(glob.glob(os.path.join(raw_dir, "*.json"))):
        with open(path) as f:
            data = json.load(f)
        for svc in data:
            sname = svc.get("carrierServiceName")
            for vs in svc.get("vesselSchedules", []):
                v = vs["vessel"]
                for seq, c in enumerate(vs.get("transportCalls", [])):
                    ref = c.get("transportCallReference")
                    if ref in seen:
                        continue
                    seen.add(ref)
                    ts = {}
                    for t in c.get("timestamps", []):
                        k = (t["eventTypeCode"], t["eventClassifierCode"])
                        ts[k] = datetime.fromisoformat(t["eventDateTime"])
                    g = lambda e, cl: ts.get((e, cl))
                    pa, ea, aa = g("ARRI", "PLN"), g("ARRI", "EST"), g("ARRI", "ACT")
                    pd_, ed, ad = g("DEPA", "PLN"), g("DEPA", "EST"), g("DEPA", "ACT")
                    loc = c["location"]
                    row = {
                        "service": sname,
                        "vessel": v["name"].title().replace("Maersk", "Maersk"),
                        "imo": v["vesselIMONumber"],
                        "flag": v.get("flag"),
                        "seq": seq,
                        "ref": ref,
                        "voyage_in": c.get("carrierImportVoyageNumber"),
                        "voyage_out": c.get("carrierExportVoyageNumber"),
                        "port": loc.get("UNLocationCode"),
                        "port_name": PORTS.get(loc.get("UNLocationCode"), {}).get("name", loc.get("locationName")),
                        "terminal": loc.get("facilityName") or loc.get("locationName"),
                        "pln_arr": _iso(pa), "est_arr": _iso(ea), "act_arr": _iso(aa),
                        "pln_dep": _iso(pd_), "est_dep": _iso(ed), "act_dep": _iso(ad),
                        "t_pln_arr": _hours(pa), "t_act_arr": _hours(aa),
                        "t_pln_dep": _hours(pd_), "t_act_dep": _hours(ad),
                        "t_est_arr": _hours(ea), "t_est_dep": _hours(ed),
                    }
                    row["arr_delay_h"] = _r(row["t_act_arr"] - row["t_pln_arr"]) if aa and pa else None
                    row["dep_delay_h"] = _r(row["t_act_dep"] - row["t_pln_dep"]) if ad and pd_ else None
                    row["stay_act_h"] = _r(row["t_act_dep"] - row["t_act_arr"]) if aa and ad else None
                    row["stay_pln_h"] = _r(row["t_pln_dep"] - row["t_pln_arr"]) if pa and pd_ else None
                    row["status"] = "completed" if ad else ("in_port" if aa else "planned")
                    calls.append(row)
    # normalise vessel naming
    for c in calls:
        n = c["vessel"]
        c["vessel"] = " ".join(w.capitalize() for w in n.split())
    return calls


def _blocks(calls):
    """Group calls by (vessel, service) keeping file order."""
    out = {}
    for c in calls:
        out.setdefault((c["vessel"], c["service"]), []).append(c)
    for k in out:
        out[k].sort(key=lambda c: c["t_pln_arr"] or 0)
    return out


def build_legs(calls):
    legs = []
    for (vessel, svc), blk in _blocks(calls).items():
        if svc not in SERVICES:
            continue
        for a, b in zip(blk, blk[1:]):
            if a["t_act_dep"] is None or b["t_act_arr"] is None:
                continue
            d = dist_nm(a["port"], b["port"])
            act = b["t_act_arr"] - a["t_act_dep"]
            pln = (b["t_pln_arr"] - a["t_pln_dep"]) if (a["t_pln_dep"] and b["t_pln_arr"]) else None
            v_avg = d / act if act > 0 else None
            sail_v = max(v_avg or 0, PARAMS["v_min"])
            wait = max(0.0, act - d / PARAMS["v_ref_wait"])
            legs.append({
                "service": svc, "vessel": vessel,
                "from": a["port"], "to": b["port"],
                "from_name": a["port_name"], "to_name": b["port_name"],
                "voyage": a["voyage_out"],
                "dist_nm": d,
                "pln_h": _r(pln), "act_h": _r(act),
                "recovered_h": _r(pln - act) if pln else None,
                "pln_speed_kn": _r(d / pln, 2) if pln else None,
                "avg_speed_kn": _r(v_avg, 2),
                "sail_speed_kn": _r(min(sail_v, 24.0), 2),
                "est_wait_h": _r(wait),
                "dep_delay_h": a["dep_delay_h"],
                "arr_delay_h": b["arr_delay_h"],
                "t_dep": a["t_act_dep"], "dep_iso": a["act_dep"], "arr_iso": b["act_arr"],
            })
    return legs


def detect_omissions(calls):
    """Ports in the rotation that a vessel sailed past (skipped calls)."""
    out = []
    for (vessel, svc), blk in _blocks(calls).items():
        if svc not in SERVICES:
            continue
        rot = SERVICES[svc]["rotation"]
        n = len(rot)
        for a, b in zip(blk, blk[1:]):
            if a["port"] not in rot or b["port"] not in rot:
                continue
            ia, ib = rot.index(a["port"]), rot.index(b["port"])
            gap = (ib - ia) % n
            if gap > 1:
                skipped = [rot[(ia + k) % n] for k in range(1, gap)]
                pln = (b["t_pln_arr"] - a["t_pln_dep"]) if (a["t_pln_dep"] and b["t_pln_arr"]) else None
                out.append({
                    "service": svc, "vessel": vessel, "voyage": a["voyage_out"],
                    "from": a["port"], "to": b["port"],
                    "skipped": skipped,
                    "skipped_names": [PORTS[p]["name"] for p in skipped],
                    "from_dep_delay_h": a["dep_delay_h"],
                    "to_arr_delay_h": b["arr_delay_h"],
                    "planned_gap_h": _r(pln),
                    "date": a["act_dep"] or a["pln_dep"],
                    "status": "completed" if b["t_act_arr"] else "planned",
                })
    return out


def build_proforma(calls):
    """Median planned stay per port and planned transit per rotation leg."""
    pro = {}
    for svc, cfg in SERVICES.items():
        rot = cfg["rotation"]
        stays = {p: [] for p in rot}
        legs = {p: [] for p in rot}  # leg from p to next rotation port
        for (vessel, s), blk in _blocks(calls).items():
            if s != svc:
                continue
            for c in blk:
                if c["port"] in stays and c["stay_pln_h"]:
                    stays[c["port"]].append(c["stay_pln_h"])
            for a, b in zip(blk, blk[1:]):
                if a["port"] in rot and b["port"] in rot:
                    if rot[(rot.index(a["port"]) + 1) % len(rot)] == b["port"] and a["t_pln_dep"] and b["t_pln_arr"]:
                        legs[a["port"]].append(b["t_pln_arr"] - a["t_pln_dep"])
        ports = []
        for i, p in enumerate(rot):
            nxt = rot[(i + 1) % len(rot)]
            stay = st.median(stays[p]) if stays[p] else 24.0
            transit = st.median(legs[p]) if legs[p] else dist_nm(p, nxt) / 15.0
            d = dist_nm(p, nxt)
            ports.append({
                "port": p, "name": PORTS[p]["name"],
                "stay_h": round(stay, 1),
                "next": nxt, "transit_h": round(transit, 1), "dist_nm": d,
                "pln_speed_kn": round(d / transit, 2),
                "buffer_h": round(transit - d / 18.0, 1),   # time spare vs 18 kn
                "teu": round(stay * PARAMS["berth_prod_teu_h"]),
            })
        loop_h = sum(p["stay_h"] + p["transit_h"] for p in ports)
        pro[svc] = {"ports": ports, "loop_h": round(loop_h, 1), "loop_days": round(loop_h / 24, 1)}
    return pro


def port_stats(calls, legs):
    out = {}
    for c in calls:
        if c["service"] not in SERVICES or c["arr_delay_h"] is None:
            continue
        k = (c["service"], c["port"])
        out.setdefault(k, {"service": c["service"], "port": c["port"], "name": c["port_name"],
                           "arr_delays": [], "stay_act": [], "stay_pln": [], "waits": []})
        out[k]["arr_delays"].append(c["arr_delay_h"])
        if c["stay_act_h"] is not None:
            out[k]["stay_act"].append(c["stay_act_h"])
        if c["stay_pln_h"] is not None:
            out[k]["stay_pln"].append(c["stay_pln_h"])
    for lg in legs:
        k = (lg["service"], lg["to"])
        if k in out:
            out[k]["waits"].append(lg["est_wait_h"])
    res = []
    for v in out.values():
        a = v["arr_delays"]
        res.append({
            "service": v["service"], "port": v["port"], "name": v["name"], "n": len(a),
            "arr_delays": a,
            "mean_delay": _r(st.mean(a)), "median_delay": _r(st.median(a)), "max_delay": _r(max(a)),
            "mean_stay_act": _r(st.mean(v["stay_act"])) if v["stay_act"] else None,
            "mean_stay_pln": _r(st.mean(v["stay_pln"])) if v["stay_pln"] else None,
            "waits": v["waits"],
            "mean_wait": _r(st.mean(v["waits"])) if v["waits"] else 0.0,
        })
    return res


def load_all():
    calls = load_calls()
    legs = build_legs(calls)
    return {
        "calls": calls,
        "legs": legs,
        "omissions": detect_omissions(calls),
        "proforma": build_proforma(calls),
        "port_stats": port_stats(calls, legs),
    }
