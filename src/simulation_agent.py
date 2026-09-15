"""
simulation_agent.py
===================
Multi-agent heuristic simulation of vessel schedule recovery.

Directly implements equations from:
  Kowatari & Shibasaki (2025) — Schedule Recovery Strategies in Container
  Liner Shipping Networks Using Multi-Agent Simulations
  2025 IEEE International Conference on Big Data

Equations implemented:
  Eq. 6  — Speed adjustment function (delay-responsive acceleration + queue deceleration)
  Eq. 7  — Port skipping threshold
  Eq. 8  — Congestion factor C_p(q)
  Eq. 9  — Berthing time with congestion and handling efficiency
  Eq.10  — Fuel consumption FC = δ·√(10·TEU)·V³

Used for:
  1. Scenario analysis to generate Pareto trade-off curve (delay vs fuel)
  2. Training data generation for ML classifier
"""

import numpy as np
import json
from dataclasses import dataclass, field
from collections import deque
from typing import List, Dict, Tuple, Optional

# ─────────────────────────────────────────────────────────────────────────────
# LOAD METADATA
# ─────────────────────────────────────────────────────────────────────────────
try:
    with open("data/route_metadata.json") as f:
        META = json.load(f)
    PORTS         = META["ports"]
    LEG_DISTANCES = META["leg_distances"]
    N_VESSELS     = META["n_vessels"]
    TEU           = META["teu_capacity"]
    V_DEFAULT     = META["v_default"]
    V_MIN         = META["v_min"]
    V_MAX         = META["v_max"]
    T_BERTH_BASE  = META["t_berth_base"]
    DELTA         = META["delta"]
    BUNKER_PRICE  = META["bunker_price_usd_mt"]
except FileNotFoundError:
    # Fallback defaults
    PORTS = [
        {"code": "JPYOK", "name": "Yokohama"},
        {"code": "CNSHA", "name": "Shanghai"},
        {"code": "CNNBO", "name": "Ningbo"},
        {"code": "CNYTN", "name": "Yantian"},
        {"code": "USLAX", "name": "Los Angeles"},
        {"code": "USLGB", "name": "Long Beach"},
        {"code": "USOAK", "name": "Oakland"},
    ]
    LEG_DISTANCES = [1094, 97, 734, 5445, 21, 386, 4536]
    N_VESSELS=10; TEU=14000; V_DEFAULT=18.3; V_MIN=10; V_MAX=25
    T_BERTH_BASE=42; DELTA=5.813e-5; BUNKER_PRICE=650

N_PORTS = len(PORTS)
TWK     = 168    # weekly headway hours


# ─────────────────────────────────────────────────────────────────────────────
# AGENTS
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class VesselAgent:
    id: int
    service_port_sequence: List[int]   # indices into PORTS
    teu: int = 14000

    # State
    state: str            = "NOT_DEPARTED"   # NOT_DEPARTED | EN_ROUTE | ANCHORED | BERTHED
    current_port_idx: int = 0               # current position in sequence
    position_nm: float    = 0.0             # nm traveled on current leg
    speed: float          = V_DEFAULT
    delay: float          = 0.0             # accumulated delay hours
    total_fuel: float     = 0.0             # ton
    skips: int            = 0
    departure_time: float = 0.0             # sim time when last departed
    log: List[Dict]       = field(default_factory=list)

    def leg_distance(self) -> float:
        """Distance of current leg."""
        seq  = self.service_port_sequence
        from_p = seq[(self.current_port_idx - 1) % len(seq)]
        to_p   = seq[self.current_port_idx % len(seq)]
        # leg index in full PORTS list → LEG_DISTANCES index
        return LEG_DISTANCES[to_p % len(LEG_DISTANCES)]

    def fuel_rate(self) -> float:
        """Eq. 10: FC = δ·√(10·TEU)·V³  (ton/hour)"""
        return DELTA * np.sqrt(10 * self.teu) * (self.speed ** 3)


@dataclass
class PortAgent:
    idx: int
    name: str
    n_berths: int = 4
    queue: deque  = field(default_factory=deque)
    berthed: List = field(default_factory=list)
    H_p: float    = 1.0
    queue_log: List = field(default_factory=list)

    # Congestion parameters (Eq. 8)
    L: int   = 3      # low threshold
    H_q: int = 10     # high threshold  (renamed to avoid collision with H_p)
    m: float = 0.7    # minimum congestion factor

    def congestion_factor(self, q: int) -> float:
        """Eq. 8: C_p(q)"""
        if q <= self.L:
            return 1.0
        elif q <= self.H_q:
            return 1.0 - ((1.0 - self.m) / (self.H_q - self.L)) * (q - self.L)
        else:
            return self.m

    def berth_time(self, t_base: float, q: int) -> float:
        """Eq. 9: T_berth = T_berth_s · C_p(q) · H_p"""
        return t_base * self.congestion_factor(q) * self.H_p


# ─────────────────────────────────────────────────────────────────────────────
# SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
class VSRPSimulation:
    def __init__(
        self,
        alpha: float = 0.03,     # acceleration coefficient
        beta: float  = 0.01,     # deceleration coefficient (queue)
        t_skip: float = 72.0,    # port skip threshold (hours)
        disruption_start: int = 2000,  # timestep when disruption begins
        disruption_port: int  = 4,     # index of LA port
        disruption_h_eff: float = 0.5, # handling efficiency during disruption
        horizon: int = 4380,     # ~6 months (Kowatari used 8760)
    ):
        self.alpha     = alpha
        self.beta      = beta
        self.t_skip    = t_skip
        self.dis_start = disruption_start
        self.dis_port  = disruption_port
        self.dis_h_eff = disruption_h_eff
        self.horizon   = horizon

        # Build port agents
        berth_counts = [5, 8, 6, 7, 4, 5, 3]   # calibrated to paper (min 3)
        self.port_agents = [
            PortAgent(
                idx=i, name=PORTS[i]["name"],
                n_berths=berth_counts[i]
            )
            for i in range(N_PORTS)
        ]

        # Build vessel agents
        seq = list(range(N_PORTS))
        self.vessel_agents = [
            VesselAgent(
                id=v,
                service_port_sequence=seq,
                departure_time=v * TWK   # staggered weekly
            )
            for v in range(N_VESSELS)
        ]

        # Scheduled arrival times (for delay computation)
        self.sched = self._build_schedule()

        # Metrics
        self.delay_series: List[float] = []
        self.fuel_series:  List[float] = []
        self.skip_events:  List[Dict]  = []

    def _build_schedule(self):
        """Pre-compute nominal arrival times per vessel per port."""
        sched = {}
        for v in range(N_VESSELS):
            t = v * TWK
            for p in range(N_PORTS):
                if p > 0:
                    t += LEG_DISTANCES[p - 1] / V_DEFAULT
                sched[(v, p)] = t
                t += T_BERTH_BASE
        return sched

    def _adjust_speed(self, vessel: VesselAgent, dest_port_idx: int) -> float:
        """
        Eq. 6: v = clip( v_def · (1 + α·D) · (1 - β·Q), v_min, v_max )
        """
        q = len(self.port_agents[dest_port_idx].queue)
        v = V_DEFAULT * (1 + self.alpha * vessel.delay) * (1 - self.beta * q)
        return float(np.clip(v, V_MIN, V_MAX))

    def _check_skip(self, vessel: VesselAgent) -> bool:
        """Eq. 7: skip if delay >= T_skip"""
        return vessel.delay >= self.t_skip

    def step(self, t: int):
        """Advance simulation by 1 hour."""

        # Apply disruption to port agent
        if t >= self.dis_start:
            self.port_agents[self.dis_port].H_p = self.dis_h_eff
            # Also propagate partial congestion to adjacent port
            if self.dis_port + 1 < N_PORTS:
                self.port_agents[self.dis_port + 1].H_p = 0.75
        else:
            for pa in self.port_agents:
                pa.H_p = 1.0

        for vessel in self.vessel_agents:
            if vessel.state == "NOT_DEPARTED" and t >= vessel.departure_time:
                vessel.state      = "EN_ROUTE"
                vessel.speed      = V_DEFAULT
                vessel.position_nm = 0.0
                vessel.current_port_idx = 0

            elif vessel.state == "EN_ROUTE":
                # Advance position
                vessel.position_nm += vessel.speed  # 1-hour step
                leg_dist = LEG_DISTANCES[vessel.current_port_idx % N_PORTS]

                if vessel.position_nm >= leg_dist:
                    # Arrived at destination port
                    dest = vessel.current_port_idx % N_PORTS
                    vessel.position_nm = 0.0

                    # Check scheduled arrival
                    sched_arr = self.sched.get((vessel.id, dest), t)
                    delay_here = max(0, t - sched_arr)
                    vessel.delay = delay_here

                    # Check skip
                    next_dest = (dest + 1) % N_PORTS
                    if self._check_skip(vessel) and dest not in (0, N_PORTS - 1):
                        vessel.skips += 1
                        self.skip_events.append({
                            "t": t, "vessel": vessel.id,
                            "skipped_port": PORTS[dest]["name"],
                            "delay_at_skip": vessel.delay,
                        })
                        vessel.current_port_idx = (vessel.current_port_idx + 2) % N_PORTS
                        vessel.speed = self._adjust_speed(vessel, vessel.current_port_idx % N_PORTS)
                    else:
                        pa = self.port_agents[dest]
                        pa.queue.append(vessel.id)
                        vessel.state = "ANCHORED"

            elif vessel.state == "ANCHORED":
                dest = vessel.current_port_idx % N_PORTS
                pa   = self.port_agents[dest]

                if len(pa.berthed) < pa.n_berths and pa.queue and pa.queue[0] == vessel.id:
                    pa.queue.popleft()
                    pa.berthed.append(vessel.id)
                    vessel.state = "BERTHED"
                    q = len(pa.queue) + len(pa.berthed)
                    vessel._berth_until = t + pa.berth_time(T_BERTH_BASE, q)

            elif vessel.state == "BERTHED":
                dest = vessel.current_port_idx % N_PORTS
                pa   = self.port_agents[dest]
                if t >= vessel._berth_until:
                    pa.berthed.remove(vessel.id)
                    vessel.current_port_idx = (vessel.current_port_idx + 1) % N_PORTS
                    vessel.state = "EN_ROUTE"
                    vessel.speed = self._adjust_speed(vessel, vessel.current_port_idx % N_PORTS)

            # Accumulate fuel (if sailing)
            if vessel.state == "EN_ROUTE":
                vessel.total_fuel += vessel.fuel_rate()

        # Log hourly metrics
        avg_delay = np.mean([v.delay for v in self.vessel_agents])
        avg_fuel  = np.mean([v.fuel_rate() for v in self.vessel_agents
                             if v.state == "EN_ROUTE"] or [0])
        self.delay_series.append(avg_delay)
        self.fuel_series.append(avg_fuel)

        for pa in self.port_agents:
            pa.queue_log.append(len(pa.queue))

    def run(self) -> Dict:
        for t in range(self.horizon):
            self.step(t)

        avg_delay      = float(np.mean(self.delay_series))
        avg_fuel_rate  = float(np.mean(self.fuel_series))
        total_skips    = sum(v.skips for v in self.vessel_agents)
        peak_queue_la  = max(self.port_agents[self.dis_port].queue_log or [0])

        return {
            "alpha"          : self.alpha,
            "beta"           : self.beta,
            "t_skip"         : self.t_skip,
            "avg_delay_hrs"  : round(avg_delay, 2),
            "avg_fuel_ton_hr": round(avg_fuel_rate, 4),
            "total_skips"    : total_skips,
            "skip_events"    : self.skip_events[:20],   # top 20
            "peak_queue_la"  : peak_queue_la,
            "delay_series"   : self.delay_series[::50],   # every 50 hrs
            "fuel_series"    : self.fuel_series[::50],
        }


# ─────────────────────────────────────────────────────────────────────────────
# PARETO SWEEP  (reproduces Kowatari Fig. 5)
# ─────────────────────────────────────────────────────────────────────────────
def run_pareto_sweep(horizon: int = 2000) -> List[Dict]:
    """
    Sweep (α, β) combinations and return delay–fuel Pareto data.
    Kowatari used 25 combinations; we use 9 for speed.
    """
    alphas = [0.005, 0.03, 0.08]
    betas  = [0.005, 0.03, 0.08]
    results = []

    print("Running Pareto sweep...")
    for a in alphas:
        for b in betas:
            sim = VSRPSimulation(alpha=a, beta=b, horizon=horizon)
            r   = sim.run()
            results.append({
                "alpha"    : a,
                "beta"     : b,
                "label"    : f"α={a}, β={b}",
                "delay"    : r["avg_delay_hrs"],
                "fuel"     : r["avg_fuel_ton_hr"],
                "skips"    : r["total_skips"],
            })
            print(f"  α={a:.3f} β={b:.3f} → delay={r['avg_delay_hrs']:.1f}h fuel={r['avg_fuel_ton_hr']:.3f} t/h skips={r['total_skips']}")

    return results


def run_skip_threshold_sweep(horizon: int = 2000) -> List[Dict]:
    """Reproduces Kowatari S2 scenario analysis."""
    results = []
    for t_skip in [24, 72, 120, 168]:
        sim = VSRPSimulation(t_skip=t_skip, horizon=horizon)
        r   = sim.run()
        results.append({
            "t_skip" : t_skip,
            "delay"  : r["avg_delay_hrs"],
            "fuel"   : r["avg_fuel_ton_hr"],
            "skips"  : r["total_skips"],
        })
        print(f"  T_skip={t_skip}h → skips={r['total_skips']} delay={r['avg_delay_hrs']:.1f}h")
    return results


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Running baseline simulation ===")
    sim = VSRPSimulation(horizon=2000)
    r   = sim.run()
    print(f"Avg delay  : {r['avg_delay_hrs']} hrs")
    print(f"Avg fuel   : {r['avg_fuel_ton_hr']} ton/hr")
    print(f"Total skips: {r['total_skips']}")
    print(f"Skip events (sample): {r['skip_events'][:3]}")

    print("\n=== Pareto sweep ===")
    run_pareto_sweep()

    print("\n=== Skip threshold sweep ===")
    run_skip_threshold_sweep()
