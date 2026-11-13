"""
milp.py — a very small modelling layer on top of scipy.optimize.milp (HiGHS).

Keeps the recovery model readable (named variables, linear expressions as
dicts) without needing an external solver binary, so it runs anywhere
SciPy runs (laptop, Render, Vercel).
"""

import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import coo_matrix


class Model:
    def __init__(self):
        self.names, self.lb, self.ub, self.integ, self.obj = [], [], [], [], []
        self.rows, self.rlo, self.rhi = [], [], []
        self.const = 0.0

    def var(self, name, lb=0.0, ub=np.inf, binary=False, cost=0.0):
        self.names.append(name)
        self.lb.append(0.0 if binary else lb)
        self.ub.append(1.0 if binary else ub)
        self.integ.append(1 if binary else 0)
        self.obj.append(cost)
        return len(self.names) - 1

    def fix(self, idx, value):
        self.lb[idx] = value
        self.ub[idx] = value

    def add(self, expr, lo=-np.inf, hi=np.inf):
        """expr: dict {var_index: coefficient}; lo <= expr <= hi."""
        self.rows.append(expr)
        self.rlo.append(lo)
        self.rhi.append(hi)

    def solve(self, time_limit=10.0):
        n = len(self.names)
        r, c, v = [], [], []
        for i, row in enumerate(self.rows):
            for j, a in row.items():
                r.append(i); c.append(j); v.append(a)
        A = coo_matrix((v, (r, c)), shape=(len(self.rows), n)).tocsr()
        res = milp(
            c=np.array(self.obj),
            constraints=[LinearConstraint(A, np.array(self.rlo), np.array(self.rhi))],
            integrality=np.array(self.integ),
            bounds=Bounds(np.array(self.lb), np.array(self.ub)),
            options={"time_limit": time_limit, "disp": False, "mip_rel_gap": 1e-6},
        )
        return res
