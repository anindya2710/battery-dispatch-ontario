"""A fair contest between the optimizer and the threshold rule.

In the main comparison the threshold rule acts on each hour's ACTUAL price,
which nobody knows until the hour is over, while the persistence optimizer only
sees yesterday's prices. That gives the rule an advantage that has nothing to do
with optimization.

Here both act on the same information, IESO's pre-dispatch prices:

threshold rule, pre-dispatch
    Charge when the 1-hour-ahead pre-dispatch price for the coming hour is at or
    below one threshold, discharge when it is at or above another. Thresholds
    are tuned on January-June, as before.

optimizer, pre-dispatch
    At the start of every hour, re-plan the rest of the day with the MILP and
    carry out only the first hour of the plan. The forecast for the coming hour
    is its 1-hour-ahead pre-dispatch price, for the next two hours the 2- and
    3-hour-ahead prices, and for the hours after that yesterday's actual prices
    (persistence). The plan starts from the battery's actual state of charge
    and must end the day at the usual start level.

At the start of hour h, all three pre-dispatch prices used were published during
hour h-1, and yesterday's prices are long known, so neither strategy uses
anything it could not have had at the time. Both are settled at actual prices.
"""
import numpy as np

from .battery import Battery
from .model import DailyDispatchModel, Schedule
from .strategies import idle


def fill_with_persistence(forecast: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Replace missing forecasts with yesterday's actual price for the same hour.

    Both arrays have shape (days, hours). Day 0 has no yesterday, so any gap
    there is filled with that day's own first available forecast value, and
    the strategies leave day 0 idle anyway.
    """
    out = np.array(forecast, dtype=float)
    yesterday = np.vstack([actual[:1], actual[:-1]])
    gaps = np.isnan(out)
    out[gaps] = yesterday[gaps]
    return out


def rolling_optimizer(actual: np.ndarray, predispatch: dict, battery: Battery) -> list:
    """Hourly re-planning on pre-dispatch prices. One schedule per day; day 0 idles.

    actual: (days, hours) actual prices, used only as yesterday's prices.
    predispatch: {1: (days, hours), 2: ..., 3: ...}, k-hour-ahead prices.
    """
    days, hours = actual.shape
    b = battery
    pd1, pd2, pd3 = (fill_with_persistence(predispatch[k], actual) for k in (1, 2, 3))
    models = {h: DailyDispatchModel(b, hours=h) for h in range(1, hours + 1)}
    schedules = [idle(hours, b)]
    for k in range(1, days):
        charge, discharge, soc = np.zeros(hours), np.zeros(hours), np.zeros(hours)
        s = b.s_start
        for t in range(hours):
            forecast = actual[k - 1, t:].copy()          # persistence for the later hours
            forecast[0] = pd1[k, t]
            if t + 1 < hours:
                forecast[1] = pd2[k, t + 1]
            if t + 2 < hours:
                forecast[2] = pd3[k, t + 2]
            plan = models[hours - t].solve(forecast, s0=s)
            c, d = float(plan.charge[0]), float(plan.discharge[0])
            s = s + b.eta_c * c - d / b.eta_d
            charge[t], discharge[t], soc[t] = c, d, s
        schedules.append(Schedule(charge=charge, discharge=discharge, soc=soc))
    return schedules
