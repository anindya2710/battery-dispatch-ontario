"""Hourly re-planning on IESO's pre-dispatch prices, and threshold rules on the same prices.

IESO publishes pre-dispatch prices: projections of each hour's price made one,
two and three hours in advance. Two strategies act on them:

re-planning optimizer
    At the start of every hour, re-plan the rest of the day with the MILP and
    carry out only the first hour of the plan. The hours that have a
    pre-dispatch price get it; every later hour gets yesterday's actual price
    for that hour (persistence). The plan starts from the battery's actual
    state of charge and must end the day at the usual start level.
threshold rule on pre-dispatch prices
    Charge when the pre-dispatch price for the coming hour is at or below one
    threshold, discharge when it is at or above another. The thresholds are
    tuned on January-June, as for the rule on actual prices.

Both are settled at actual prices, and both are run with two kinds of timing:

1-hour-ahead
    The decision for hour h uses the prices published during hour h-1: the 1-,
    2- and 3-hour-ahead prices for hours h, h+1 and h+2.
3-hour-ahead
    The decision for hour h uses only the 3-hour-ahead price for hour h,
    published during hour h-3. Ontario's market rules in 2024 locked a
    participant's offers and bids for an hour two hours before it started
    (the "mandatory window"; changes after that needed IESO's approval), so
    only this timing is one a real battery could have acted on.

Neither strategy uses anything published after its decision was due. Bidding,
dispatch instructions and the five-minute real-time market are not modelled.
"""
import numpy as np

from .battery import Battery
from .model import DailyDispatchModel, Schedule
from .strategies import idle

ONE_HOUR_AHEAD = (1, 2, 3)      # horizons used for hours h, h+1, h+2
THREE_HOURS_AHEAD = (3,)        # horizon used for hour h only


def fill_with_persistence(forecast: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Replace missing forecasts with yesterday's actual price for the same hour.

    Both arrays have shape (days, hours). Day 0 has no yesterday, so gaps on
    day 0 are left missing (NaN); run.py does not evaluate day 0.
    """
    out = np.array(forecast, dtype=float)
    gaps = np.isnan(out)
    gaps[0] = False
    yesterday = np.vstack([np.full((1, actual.shape[1]), np.nan), actual[:-1]])
    out[gaps] = yesterday[gaps]
    return out


def remove_bias(forecast: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Subtract the forecast's average error over all EARLIER days.

    For day k, the correction is the mean of (forecast - actual) over every hour
    of days 0 to k-1 that has a forecast, so it uses only what was known before
    day k began. Day 0 has no history and is left as it is. Missing values stay
    missing.
    """
    f = np.array(forecast, dtype=float)
    err = f - actual
    total = np.concatenate([[0.0], np.cumsum(np.nansum(err, axis=1))[:-1]])
    count = np.concatenate([[0], np.cumsum((~np.isnan(err)).sum(axis=1))[:-1]])
    bias = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    return f - bias[:, None]


def rolling_optimizer(actual: np.ndarray, predispatch: dict, battery: Battery,
                      ahead=ONE_HOUR_AHEAD) -> list:
    """Hourly re-planning on pre-dispatch prices. One schedule per day; day 0 idles.

    actual: (days, hours) actual prices, used only as yesterday's prices.
    predispatch: {1: (days, hours), 2: ..., 3: ...}, k-hour-ahead prices.
    ahead: which horizon to use for hour h, h+1, ... in each plan:
        (1, 2, 3) uses the 1-, 2- and 3-hour-ahead prices for hours h, h+1, h+2;
        (3,) uses only the 3-hour-ahead price, for hour h.
    """
    days, hours = actual.shape
    b = battery
    filled = {k: fill_with_persistence(predispatch[k], actual) for k in set(ahead)}
    models = {h: DailyDispatchModel(b, hours=h) for h in range(1, hours + 1)}
    schedules = [idle(hours, b)]
    for k in range(1, days):
        charge, discharge, soc = np.zeros(hours), np.zeros(hours), np.zeros(hours)
        s = b.s_start
        for t in range(hours):
            forecast = actual[k - 1, t:].copy()          # persistence for the later hours
            for j, horizon in enumerate(ahead):
                if t + j < hours:
                    forecast[j] = filled[horizon][k, t + j]
            plan = models[hours - t].solve(forecast, s0=s)
            c, d = float(plan.charge[0]), float(plan.discharge[0])
            s = s + b.eta_c * c - d / b.eta_d
            charge[t], discharge[t], soc[t] = c, d, s
        schedules.append(Schedule(charge=charge, discharge=discharge, soc=soc))
    return schedules
