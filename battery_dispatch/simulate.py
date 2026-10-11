"""Turning a schedule into money, and the simple threshold rule.

Every strategy is judged the same way: its hourly charge and discharge are
settled at the ACTUAL prices, whatever prices it was planned on.
"""
import numpy as np

from .battery import Battery
from .model import Schedule

TOL = 1e-6


def check_feasible(schedule: Schedule, battery: Battery) -> None:
    """Raise if a schedule breaks the battery's physical limits."""
    b = battery
    c, d = schedule.charge, schedule.discharge
    if (c < -TOL).any() or (d < -TOL).any():
        raise ValueError("negative charge or discharge")
    if (c > b.power_mw + TOL).any() or (d > b.power_mw + TOL).any():
        raise ValueError("power limit exceeded")
    soc = b.s_start + np.cumsum(b.eta_c * c - d / b.eta_d)
    if (soc < b.s_min - TOL).any() or (soc > b.s_max + TOL).any():
        raise ValueError("state of charge outside its limits")
    if abs(soc[-1] - b.s_start) > TOL:
        raise ValueError("day does not end at the starting state of charge")


def settle(schedule: Schedule, actual_prices, battery: Battery) -> dict:
    """Money and wear for one day, at actual prices."""
    p = np.asarray(actual_prices, dtype=float)
    c, d = schedule.charge, schedule.discharge
    market = float(np.sum(p * (d - c)))
    throughput = float(np.sum(c + d))
    wear = battery.degradation_cost * throughput
    return {
        "market_revenue": market,                    # what the market pays
        "degradation_cost": wear,                    # what the wear costs
        "net_revenue": market - wear,
        "charged_mwh": float(c.sum()),
        "discharged_mwh": float(d.sum()),
        # one "cycle" = the usable stored energy emptied once
        "cycles": float(np.sum(d / battery.eta_d) / battery.usable_mwh),
    }


def threshold_schedule(prices, low: float, high: float, battery: Battery) -> Schedule:
    """Charge at full power when price <= low, discharge when price >= high.

    The rule looks at one hour at a time and knows nothing about later prices.
    It is clipped so that the battery stays inside its limits and is forced to
    discharge late in the day when that is the only way to end the day at the
    starting state of charge, the same condition the optimizer has to meet.
    """
    b = battery
    p = np.asarray(prices, dtype=float)
    n = len(p)
    charge, discharge, soc = np.zeros(n), np.zeros(n), np.zeros(n)
    s = b.s_start
    per_hour_out = b.power_mw / b.eta_d          # stored MWh one hour of full discharge removes

    for t in range(n):
        can_dump_later = (n - t - 1) * per_hour_out
        c = d = 0.0
        if p[t] <= low:
            # room below the upper limit, and no more than can be emptied again today
            room = min(b.s_max - s, can_dump_later - (s - b.s_start))
            c = max(0.0, min(b.power_mw, room / b.eta_c))
        elif p[t] >= high:
            # never below the starting level, so the day can always end where it began
            d = max(0.0, min(b.power_mw, (s - b.s_start) * b.eta_d))

        must_remove_now = (s - b.s_start) - can_dump_later
        if must_remove_now > 1e-12:
            c = 0.0
            d = max(d, min(b.power_mw, must_remove_now * b.eta_d))

        s = s + b.eta_c * c - d / b.eta_d
        charge[t], discharge[t], soc[t] = c, d, s

    return Schedule(charge=charge, discharge=discharge, soc=soc)
