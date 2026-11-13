import numpy as np
from scipy.optimize import minimize
from src.vsrp_optimizer import total_cost, V_DEFAULT, V_MAX, V_MIN, N_LEGS

def objective(v):
    return total_cost(v, [1, 3], 140)["total"]

v0 = np.full(N_LEGS, min(V_DEFAULT * 1.08, V_MAX))
print("v0:", v0)
print("Cost at v0:", objective(v0))

res = minimize(objective, v0, method="SLSQP", bounds=[(V_MIN, V_MAX)] * N_LEGS)
print("SLSQP Result:", res.x)
print("SLSQP Success:", res.success, res.message)

res_lbfgsb = minimize(objective, v0, method="L-BFGS-B", bounds=[(V_MIN, V_MAX)] * N_LEGS)
print("L-BFGS-B Result:", res_lbfgsb.x)
print("L-BFGS-B Success:", res_lbfgsb.success, res_lbfgsb.message)
