"""
routes.py
=========
Static facts about the two Maersk transpacific services used in this study,
plus every modelling parameter, each tagged with where it comes from:

  REAL     – read directly from the Maersk TP7/TP8 schedule files
  DERIVED  – computed from real data or a public tool (searoute distances)
  PUBLIC   – public reference value, still to be verified
  ASSUMED  – literature / engineering assumption (sensitivity-tested)
"""

import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "TP7_TP8 Vessel Data")
OUT_DIR = os.path.join(ROOT, "data", "real")

# ─────────────────────────────────────────────────────────────────────────────
# PORTS  (terminal coordinates, lon/lat)  — PUBLIC (UN/LOCODE + terminal location)
# ─────────────────────────────────────────────────────────────────────────────
PORTS = {
    "CNTXG": {"name": "Tianjin",     "country": "China",       "lon": 117.78, "lat": 38.98},
    "CNTAO": {"name": "Qingdao",     "country": "China",       "lon": 120.17, "lat": 36.00},
    "KRPUS": {"name": "Busan",       "country": "South Korea", "lon": 128.82, "lat": 35.07},
    "CNSHA": {"name": "Shanghai",    "country": "China",       "lon": 122.07, "lat": 30.62},
    "CNNGB": {"name": "Ningbo",      "country": "China",       "lon": 121.98, "lat": 29.77},
    "JPYOK": {"name": "Yokohama",    "country": "Japan",       "lon": 139.66, "lat": 35.45},
    "USLAX": {"name": "Los Angeles", "country": "USA",         "lon": -118.25, "lat": 33.73},
    "USOAK": {"name": "Oakland",     "country": "USA",         "lon": -122.32, "lat": 37.80},
}

# ─────────────────────────────────────────────────────────────────────────────
# SERVICES  — rotation is REAL (observed call sequence in the schedule files)
# ─────────────────────────────────────────────────────────────────────────────
SERVICES = {
    "TP8": {
        "code": "708",
        "name": "TP8 · Transpacific 8",
        "rotation": ["CNTXG", "CNTAO", "KRPUS", "CNSHA", "USLAX", "USOAK"],
        "loop_days": 56,          # DERIVED: Gustav Maersk LAX→LAX = 56 days
        "fleet_estimate": 8,      # DERIVED: 56-day loop / weekly headway
        "teu": 11000,             # PUBLIC: Port of LA rotation listing 11,008–11,294 TEU
        "mandatory": ["USLAX"],
    },
    "TP7": {
        "code": "707",
        "name": "TP7 · Transpacific 7",
        "rotation": ["JPYOK", "KRPUS", "CNNGB", "USLAX", "USOAK"],
        "loop_days": 42,          # DERIVED: Maersk Kinloss LAX→LAX = 42 days
        "fleet_estimate": 6,
        "teu": 6500,              # ASSUMED: to verify on Equasis (IMO 9333022)
        "mandatory": ["USLAX"],
    },
}

# THE Alliance FP1 rotation used in Watanabe (2024) / Kowatari (2025), for comparison
PAPER_ROUTE = ["Yokohama", "Shanghai", "Ningbo", "Yantian", "Los Angeles", "Long Beach", "Oakland"]

# ─────────────────────────────────────────────────────────────────────────────
# MODEL PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────
PARAMS = {
    # vessel / fuel
    "delta":            5.813e-5,  # ASSUMED  Kowatari & Shibasaki (2025) Eq.10, tonnes/day
    "v_min":            12.0,      # ASSUMED  slow-steaming floor (kn)
    "v_max":            22.0,      # ASSUMED  operational ceiling, below ~24.5 kn design
    "speed_step":       1.0,       # discretisation of sailing options (Brouer 2013)
    "bunker_usd_t":     600.0,     # ASSUMED  VLSFO, replace with public monthly average
    # cargo / costs
    "berth_prod_teu_h": 70.0,      # ASSUMED  TEU handled per planned berth hour
    "delay_usd_teu_day": 20.0,     # ASSUMED  cargo delay cost ($/TEU/day)
    "rollover_usd_teu": 400.0,     # ASSUMED  re-route + one-week rollover cost per TEU of a skipped port
    "port_fee_usd":     80000.0,   # ASSUMED  fixed port-call fee saved when skipping
    "residual_factor":  0.5,       # ASSUMED  share of a full loop that inherits leftover delay
    "compress_max":     0.20,      # ASSUMED  max share of berth time that can be cut
    "compress_usd_h":   6000.0,    # ASSUMED  extra gangs / overtime per berth hour saved
    "max_consec_skips": 2,         # Brouer 2013 style restriction
    "early_berth_h":    24.0,      # ASSUMED  berth window opens 24h before plan
    "v_ref_wait":       16.0,      # ASSUMED  sea speed used to separate waiting from sailing
}

PROVENANCE = [
    {"item": "Port rotation, fleet sequence, voyage numbers",       "tag": "REAL",    "source": "Maersk TP7/TP8 schedule files"},
    {"item": "Planned, estimated and actual arrival/departure",     "tag": "REAL",    "source": "Maersk TP7/TP8 schedule files"},
    {"item": "Delay at every call and its propagation",             "tag": "REAL",    "source": "Actual − planned timestamps"},
    {"item": "Berth stay times (planned and actual)",               "tag": "REAL",    "source": "Departure − arrival"},
    {"item": "Omitted (skipped) port calls",                        "tag": "REAL",    "source": "Calls missing vs. rotation"},
    {"item": "Average speed per sea leg, schedule buffer",          "tag": "DERIVED", "source": "Leg time ÷ searoute distance"},
    {"item": "Anchorage waiting before congested ports",            "tag": "DERIVED", "source": "Leg time − distance ÷ 16 kn"},
    {"item": "Sea distances and route geometry",                    "tag": "DERIVED", "source": "searoute (open maritime network)"},
    {"item": "Vessel size (TP8 ≈ 11,000 TEU)",                      "tag": "PUBLIC",  "source": "Port of Los Angeles rotation listing"},
    {"item": "Bunker price",                                        "tag": "ASSUMED", "source": "Replace with public VLSFO average"},
    {"item": "Fuel–speed curve δ·√(10·TEU)·v³",                     "tag": "ASSUMED", "source": "Kowatari & Shibasaki (2025)"},
    {"item": "Cargo volume per port",                               "tag": "ASSUMED", "source": "Planned berth hours × 70 TEU/h"},
    {"item": "Delay, rollover, port-fee and handling costs",        "tag": "ASSUMED", "source": "Literature ranges, sensitivity-tested"},
    {"item": "Counterfactual outcomes for ML labels",               "tag": "SIMULATED", "source": "MILP solved on scenarios sampled from real data"},
]


# ─────────────────────────────────────────────────────────────────────────────
# FUEL
# ─────────────────────────────────────────────────────────────────────────────
def fuel_t_per_day(v_kn: float, teu: float, delta: float = PARAMS["delta"]) -> float:
    """Kowatari Eq.10: FC = δ·√(10·TEU)·v³  (tonnes per day)."""
    return delta * math.sqrt(10.0 * teu) * v_kn ** 3


def leg_fuel_t(dist_nm: float, v_kn: float, teu: float) -> float:
    hours = dist_nm / v_kn
    return fuel_t_per_day(v_kn, teu) * hours / 24.0


# ─────────────────────────────────────────────────────────────────────────────
# DISTANCES (cached; computed once with searoute)
# ─────────────────────────────────────────────────────────────────────────────
DIST_FILE = os.path.join(OUT_DIR, "distances.json")
_DIST = None


def build_distances():
    """Compute every port-pair sea distance + geometry with searoute and cache it."""
    import searoute as sr
    codes = list(PORTS)
    out = {"nm": {}, "geometry": {}}
    for a in codes:
        for b in codes:
            if a == b:
                continue
            r = sr.searoute([PORTS[a]["lon"], PORTS[a]["lat"]],
                            [PORTS[b]["lon"], PORTS[b]["lat"]], units="naut")
            key = f"{a}-{b}"
            out["nm"][key] = round(r["properties"]["length"], 1)
            out["geometry"][key] = [[round(x, 3), round(y, 3)] for x, y in r["geometry"]["coordinates"]]
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(DIST_FILE, "w") as f:
        json.dump(out, f)
    global _DIST
    _DIST = out
    return out


def distances():
    global _DIST
    if _DIST is None:
        if not os.path.exists(DIST_FILE):
            build_distances()
        with open(DIST_FILE) as f:
            _DIST = json.load(f)
    return _DIST


def dist_nm(a: str, b: str) -> float:
    return distances()["nm"][f"{a}-{b}"]
