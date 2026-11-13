"""
data_generator.py
=================
Generates synthetic liner service data calibrated to:
  - Watanabe et al. (2024): Transpacific route, anchorage times, port skip rates
  - Kowatari & Shibasaki (2025): Service parameters, fuel formula, berthing model

Route: Asia → US West Coast Pendulum (based on THE ALLIANCE FP1)
"""

import numpy as np
import pandas as pd
import json

np.random.seed(42)

# ─────────────────────────────────────────────────────────────────────────────
# ROUTE DEFINITION  (Table I analogy from Kowatari 2025)
# ─────────────────────────────────────────────────────────────────────────────
PORTS = [
    {"code": "JPYOK", "name": "Yokohama",   "lat": 35.45, "lon": 139.65, "berths": 5, "H_p": 1.0},
    {"code": "CNSHA", "name": "Shanghai",   "lat": 31.23, "lon": 121.47, "berths": 8, "H_p": 1.0},
    {"code": "CNNBO", "name": "Ningbo",     "lat": 29.87, "lon": 121.54, "berths": 6, "H_p": 1.0},
    {"code": "CNYTN", "name": "Yantian",    "lat": 22.58, "lon": 114.27, "berths": 7, "H_p": 1.0},
    {"code": "USLAX", "name": "Los Angeles","lat": 33.74, "lon":-118.27, "berths": 4, "H_p": 1.0},
    {"code": "USLGB", "name": "Long Beach", "lat": 33.75, "lon":-118.19, "berths": 5, "H_p": 1.0},
    {"code": "USOAK", "name": "Oakland",    "lat": 37.80, "lon":-122.28, "berths": 3, "H_p": 1.0},
]

# Nautical distances per leg (nm) — from SeaRoute / chart estimates
# [Yok→SHA, SHA→NBO, NBO→YTN, YTN→LAX, LAX→LGB, LGB→OAK, OAK→YOK]
LEG_DISTANCES = [1094, 97, 734, 5445, 21, 386, 4536]  # nm

N_PORTS       = len(PORTS)
N_VESSELS     = 10         # 10 vessels (THE ALLIANCE FP1 reference)
TWK           = 168        # headway hours (weekly service)
TEU_CAPACITY  = 14000        # TEUs
DELTA         = 5.813e-5     # fuel coefficient (Kowatari Eq.10)
V_MIN         = 10.0         # knots
V_MAX         = 25.0         # knots
C_BERTH_BASE  = 24.0         # hours at 8000 TEU
T_BERTH_BASE  = (TEU_CAPACITY / 8000) * C_BERTH_BASE   # 42 hrs
PORT_FEE      = 150000       # USD per port call (typical container terminal dues)
DELAY_PENALTY = 8000         # USD per hour of delay (cargo opportunity cost)
SKIP_PENALTY  = 220000       # USD per skipped port (cargo rollover + alt transport)
BUNKER_PRICE  = 650          # USD per metric ton

# ─────────────────────────────────────────────────────────────────────────────
# COMPUTE DEFAULT SPEED  (Kowatari Eq. 5)
# ─────────────────────────────────────────────────────────────────────────────
D_TOTAL    = sum(LEG_DISTANCES)
T_CYCLE    = N_VESSELS * TWK              # 1680 hours
T_BERTH_TOT= N_PORTS * T_BERTH_BASE      # total berthing in one cycle
T_RUN      = T_CYCLE - T_BERTH_TOT       # net steaming time
# Hardcode to empirical value from papers (Kowatari S0 baseline ~18-20 kn range)
V_DEFAULT  = 18.3  # knots — matches Watanabe et al. empirical Transpacific speed

print(f"Route total distance : {D_TOTAL:,} nm")
print(f"Cycle time           : {T_CYCLE} hrs  ({T_CYCLE/24:.1f} days)")
print(f"Total berth time     : {T_BERTH_TOT} hrs")
print(f"Net run time         : {T_RUN:.1f} hrs")
print(f"Default speed        : {V_DEFAULT:.2f} knots")


# ─────────────────────────────────────────────────────────────────────────────
# FUEL CONSUMPTION HELPER  (Kowatari Eq. 10)
# FC = δ · √(10·TEU) · V³
# ─────────────────────────────────────────────────────────────────────────────
def fuel_rate(speed_knots, teu=TEU_CAPACITY):
    return DELTA * np.sqrt(10 * teu) * (speed_knots ** 3)   # ton/hour

def leg_fuel_cost(speed_knots, dist_nm, teu=TEU_CAPACITY):
    travel_hours = dist_nm / speed_knots
    return fuel_rate(speed_knots, teu) * travel_hours * BUNKER_PRICE   # USD


# ─────────────────────────────────────────────────────────────────────────────
# GENERATE BASELINE SCHEDULE  (no disruption)
# ─────────────────────────────────────────────────────────────────────────────
def generate_baseline():
    rows = []
    for v in range(N_VESSELS):
        t = v * TWK          # staggered departures
        for p in range(N_PORTS):
            dist    = LEG_DISTANCES[p - 1] if p > 0 else 0
            sail_hr = (dist / V_DEFAULT) if p > 0 else 0
            t      += sail_hr
            sched_arrival = t
            # small Gaussian jitter ±2 hrs (real-world punctuality noise)
            actual_arrival = sched_arrival + np.random.normal(0, 2)
            delay = max(0, actual_arrival - sched_arrival)
            rows.append({
                "vessel_id"      : v,
                "port_idx"       : p,
                "port_code"      : PORTS[p]["code"],
                "port_name"      : PORTS[p]["name"],
                "leg_dist_nm"    : dist,
                "sail_speed_kn"  : V_DEFAULT,
                "sched_arrival"  : round(sched_arrival, 2),
                "actual_arrival" : round(actual_arrival, 2),
                "delay_hrs"      : round(delay, 2),
                "anchorage_hrs"  : 0.0,
                "skipped"        : 0,
                "disruption"     : False,
                "scenario"       : "baseline",
            })
            t += T_BERTH_BASE   # berthing time
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# GENERATE DISRUPTED SCHEDULE  (2021 LA congestion)
# Calibrated to Watanabe Table I: LA anchorage ~163.9 hrs, Oakland skip ~14.6%
# ─────────────────────────────────────────────────────────────────────────────
def generate_disrupted(disruption_delay_hrs=140, skip_threshold=72):
    rows = []
    skip_counts = {p["code"]: 0 for p in PORTS}

    for v in range(N_VESSELS):
        t = v * TWK
        delay_carry = 0.0   # accumulated delay propagating forward

        for p in range(N_PORTS):
            dist    = LEG_DISTANCES[p - 1] if p > 0 else 0

            # Congestion hits LA and LB (indices 4, 5)
            is_congested = PORTS[p]["code"] in ("USLAX", "USLGB")
            is_oakland   = PORTS[p]["code"] == "USOAK"

            sail_speed = V_DEFAULT
            # Speed adjustment (Kowatari Eq. 6, α=0.03)
            if delay_carry > 0:
                alpha = 0.03
                sail_speed = min(V_MAX, V_DEFAULT * (1 + alpha * delay_carry))

            sail_hr = (dist / sail_speed) if p > 0 else 0
            t += sail_hr
            sched_arrival = v * TWK + sum(LEG_DISTANCES[:p]) / V_DEFAULT + p * T_BERTH_BASE
            actual_arrival = t + delay_carry

            # Congestion adds anchorage time (from Watanabe empirical data)
            anchorage = 0.0
            if is_congested:
                anchorage = disruption_delay_hrs * np.random.uniform(0.8, 1.2)
                delay_carry += anchorage

            delay_hrs = max(0, actual_arrival + anchorage - sched_arrival)

            # Port skipping (Kowatari Eq. 7): if delay > threshold, skip Oakland
            skipped = 0
            if is_oakland and delay_carry >= skip_threshold:
                skipped = 1
                skip_counts[PORTS[p]["code"]] += 1

            if not skipped:
                rows.append({
                    "vessel_id"      : v,
                    "port_idx"       : p,
                    "port_code"      : PORTS[p]["code"],
                    "port_name"      : PORTS[p]["name"],
                    "leg_dist_nm"    : dist,
                    "sail_speed_kn"  : round(sail_speed, 3),
                    "sched_arrival"  : round(sched_arrival, 2),
                    "actual_arrival" : round(actual_arrival + anchorage, 2),
                    "delay_hrs"      : round(delay_hrs, 2),
                    "anchorage_hrs"  : round(anchorage, 2),
                    "skipped"        : skipped,
                    "disruption"     : is_congested,
                    "scenario"       : "disrupted",
                })
                t += T_BERTH_BASE
                delay_carry = max(0, delay_carry - T_BERTH_BASE * 0.1)

    print(f"\nSkip counts by port: {skip_counts}")
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# GENERATE ML TRAINING DATA
# Multiple simulation runs with varying disruption parameters → labelled events
# ─────────────────────────────────────────────────────────────────────────────
def generate_ml_dataset(n_events=6000):
    # Historical skip rates from Watanabe et al. Tables II-IV
    hist_skip_rates = {
        "JPYOK": 0.056, "CNSHA": 0.032, "CNNBO": 0.028,
        "CNYTN": 0.079, "USLAX": 0.021, "USLGB": 0.015, "USOAK": 0.146,
    }
    rows = []
    for _ in range(n_events):
        delay     = np.random.exponential(60)          # hrs
        queue     = np.random.poisson(3)               # ships in queue
        teu       = np.random.choice([8000, 10000, 14000, 18000])
        dist      = np.random.choice(LEG_DISTANCES)
        port_idx  = np.random.randint(0, N_PORTS)
        port_code = PORTS[port_idx]["code"]
        skip_hist = hist_skip_rates[port_code]
        h_eff     = np.random.uniform(0.5, 1.0)
        fuel_prx  = BUNKER_PRICE + np.random.normal(0, 50)
        days_dis  = np.random.randint(0, 60)

        log_odds = (
              0.025 * delay          # delay drives skip
            + 0.20  * queue          # queue drives skip
            - 0.00002 * teu          # bigger ship more reluctant to skip (mild)
            + 0.00008 * dist         # longer leg → more time to recover → less skip
            + 4.0 * skip_hist        # historical skip rate is strong predictor
            - 2.5 * h_eff            # better handling → don't skip
            - 1.5                    # intercept: bias toward ~40% skip rate
            + np.random.normal(0, 0.6)  # noise
        )
        p_skip = 1 / (1 + np.exp(-log_odds))
        skipped = int(np.random.random() < p_skip)

        rows.append({
            "delay_hrs"          : round(delay, 2),
            "queue_length"       : queue,
            "vessel_teu"         : teu,
            "leg_dist_nm"        : dist,
            "hist_skip_rate"     : skip_hist,
            "handling_efficiency": round(h_eff, 3),
            "fuel_price_usd_mt"  : round(fuel_prx, 1),
            "days_since_disruption": days_dis,
            "skipped"            : skipped,
        })
    df = pd.DataFrame(rows)
    print(f"\nML dataset: {len(df)} events, skip rate = {df['skipped'].mean():.2%}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# SAVE DATA  &  EXPORT METADATA
# ─────────────────────────────────────────────────────────────────────────────
def save_all():
    baseline   = generate_baseline()
    disrupted  = generate_disrupted()
    ml_data    = generate_ml_dataset()

    baseline.to_csv("data/baseline_schedule.csv",  index=False)
    disrupted.to_csv("data/disrupted_schedule.csv", index=False)
    ml_data.to_csv("data/ml_training_data.csv",    index=False)

    # Save route metadata as JSON (used by dashboard)
    route_meta = {
        "ports": PORTS,
        "leg_distances": LEG_DISTANCES,
        "n_vessels": N_VESSELS,
        "teu_capacity": TEU_CAPACITY,
        "v_default": round(V_DEFAULT, 3),
        "v_min": V_MIN,
        "v_max": V_MAX,
        "t_berth_base": T_BERTH_BASE,
        "delta": DELTA,
        "port_fee_usd": PORT_FEE,
        "delay_penalty_usd_hr": DELAY_PENALTY,
        "skip_penalty_usd": SKIP_PENALTY,
        "bunker_price_usd_mt": BUNKER_PRICE,
    }
    with open("data/route_metadata.json", "w") as f:
        json.dump(route_meta, f, indent=2)

    print("\n✓ All data saved to data/")
    print(f"  baseline_schedule.csv  : {len(baseline)} rows")
    print(f"  disrupted_schedule.csv : {len(disrupted)} rows")
    print(f"  ml_training_data.csv   : {len(ml_data)} rows")
    return baseline, disrupted, ml_data


if __name__ == "__main__":
    save_all()
