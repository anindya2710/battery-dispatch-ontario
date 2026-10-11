"""The daily dispatch problem as a mixed-integer linear program (MILP).

For each hour t of one day, with price p[t] in $/MWh:

    decision variables
        c[t]   power drawn from the grid to charge (MW)
        d[t]   power delivered to the grid by discharging (MW)
        s[t]   energy stored at the END of hour t (MWh)
        u[t]   1 if the battery may charge in hour t, 0 if it may discharge

    maximize    sum_t  p[t] * (d[t] - c[t])  -  kappa * (c[t] + d[t])

    subject to  s[t] = s[t-1] + eta_c * c[t] - d[t] / eta_d     (energy balance)
                s_min <= s[t] <= s_max                          (state-of-charge limits)
                c[t] <= P * u[t]                                (charge only if u = 1)
                d[t] <= P * (1 - u[t])                          (discharge only if u = 0)
                s[last hour] = s[start]                         (end the day where it began)

kappa is the degradation cost per MWh of throughput. With binary=False the u[t]
variables and their two constraints are dropped and the problem is a plain LP.
The two versions differ only when prices are negative, because only then does
charging and discharging at the same time (burning energy in the losses) pay.

The model is built once with the prices as a mutable parameter, then re-solved
for each day, which is much faster than rebuilding it 366 times.
"""
from dataclasses import dataclass

import numpy as np
import pyomo.environ as pyo

from .battery import Battery


@dataclass(frozen=True)
class Schedule:
    charge: np.ndarray       # MW drawn from the grid, one value per hour
    discharge: np.ndarray    # MW delivered to the grid, one value per hour
    soc: np.ndarray          # MWh stored at the end of each hour


class DailyDispatchModel:
    def __init__(self, battery: Battery, hours: int = 24, binary: bool = True):
        self.battery = battery
        self.hours = hours
        self.binary = binary
        b = battery

        m = pyo.ConcreteModel()
        m.T = pyo.RangeSet(0, hours - 1)
        m.price = pyo.Param(m.T, mutable=True, initialize=0.0)

        m.c = pyo.Var(m.T, bounds=(0, b.power_mw))
        m.d = pyo.Var(m.T, bounds=(0, b.power_mw))
        m.s = pyo.Var(m.T, bounds=(b.s_min, b.s_max))

        def balance(m, t):
            previous = b.s_start if t == 0 else m.s[t - 1]
            return m.s[t] == previous + b.eta_c * m.c[t] - m.d[t] / b.eta_d
        m.balance = pyo.Constraint(m.T, rule=balance)

        m.end_of_day = pyo.Constraint(expr=m.s[hours - 1] == b.s_start)

        if binary:
            m.u = pyo.Var(m.T, domain=pyo.Binary)
            m.charge_only_if_u = pyo.Constraint(m.T, rule=lambda m, t: m.c[t] <= b.power_mw * m.u[t])
            m.discharge_only_if_not_u = pyo.Constraint(
                m.T, rule=lambda m, t: m.d[t] <= b.power_mw * (1 - m.u[t]))

        m.profit = pyo.Objective(
            expr=sum(m.price[t] * (m.d[t] - m.c[t]) - b.degradation_cost * (m.c[t] + m.d[t]) for t in m.T),
            sense=pyo.maximize,
        )

        self.model = m
        self.solver = pyo.SolverFactory("appsi_highs")

    def solve(self, prices) -> Schedule:
        """Best schedule for one day of prices (a sequence of `hours` numbers)."""
        prices = np.asarray(prices, dtype=float)
        if prices.shape != (self.hours,):
            raise ValueError(f"expected {self.hours} prices, got shape {prices.shape}")
        m = self.model
        for t in m.T:
            m.price[t] = float(prices[t])

        result = self.solver.solve(m)
        if result.solver.termination_condition != pyo.TerminationCondition.optimal:
            raise RuntimeError(f"solver did not find an optimum: {result.solver.termination_condition}")

        charge = np.array([pyo.value(m.c[t]) for t in m.T])
        discharge = np.array([pyo.value(m.d[t]) for t in m.T])
        soc = np.array([pyo.value(m.s[t]) for t in m.T])
        # Solvers return values like 1e-12 instead of 0; tidy them.
        charge[np.abs(charge) < 1e-9] = 0.0
        discharge[np.abs(discharge) < 1e-9] = 0.0
        return Schedule(charge=charge, discharge=discharge, soc=soc)
