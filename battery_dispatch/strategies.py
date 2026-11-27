"""The strategies being compared.

perfect foresight   optimize each day knowing that day's actual prices. Nobody
                    can do this; it is the upper bound every other strategy is
                    measured against.
persistence         optimize each day on YESTERDAY's prices (the simplest
                    forecast there is), then settle at today's actual prices.
threshold rule      no optimization: charge below one price, discharge above
                    another. The two prices are tuned on the first part of the
                    data and tested on the rest. Here it acts on each hour's
                    actual price, which is only known once the hour is over.
re-planning optimizer and threshold rules on pre-dispatch prices: rolling.py.
do nothing          zero, for reference.
"""
import numpy as np

from .battery import Battery
from .model import DailyDispatchModel, Schedule
from .simulate import settle, threshold_schedule

# Strategy names, used in every table, figure and MLflow run.
PF = "perfect foresight"
PERSIST = "persistence"
RULE = "threshold rule, actual prices"
OPT_1H = "re-planning optimizer, 1-hour-ahead"
RULE_1H = "threshold rule, 1-hour-ahead"
OPT_3H = "re-planning optimizer, 3-hour-ahead"
RULE_3H = "threshold rule, 3-hour-ahead"
OPT_1H_UNBIASED = "re-planning optimizer, 1-hour-ahead, bias removed"
OPT_3H_UNBIASED = "re-planning optimizer, 3-hour-ahead, bias removed"
OPT_3H_EXACT = "re-planning optimizer, 3-hour-ahead, actual price as forecast"   # a check, not a strategy


def idle(hours: int, battery: Battery) -> Schedule:
    return Schedule(np.zeros(hours), np.zeros(hours), np.full(hours, battery.s_start))


def perfect_foresight(prices: np.ndarray, battery: Battery, binary: bool = True) -> list:
    """One optimal schedule per day. `prices` has shape (days, hours)."""
    model = DailyDispatchModel(battery, hours=prices.shape[1], binary=binary)
    return [model.solve(day) for day in prices]


def persistence(perfect_foresight_schedules: list, battery: Battery) -> list:
    """Schedules planned on yesterday's prices.

    The plan for day k is the optimum for day k-1's prices, which is exactly the
    perfect-foresight schedule of day k-1, so nothing needs solving twice. A
    schedule does not depend on prices to be physically valid, so it can be run
    on any day. Day 0 has no yesterday and sits idle.
    """
    hours = len(perfect_foresight_schedules[0].charge)
    return [idle(hours, battery)] + list(perfect_foresight_schedules[:-1])


def threshold(prices: np.ndarray, low: float, high: float, battery: Battery) -> list:
    return [threshold_schedule(day, low, high, battery) for day in prices]


def tune_threshold(prices: np.ndarray, battery: Battery, percentiles=range(5, 100, 5),
                   decision_prices: np.ndarray = None):
    """Pick the (low, high) price pair that earned the most on `prices`.

    Candidates are percentiles of the prices the rule acts on: `decision_prices`
    if given (for example pre-dispatch prices), otherwise `prices` themselves.
    Every pair of percentiles with low below high is tried, so the search is not
    cut off on either side.
    Revenue is always settled at `prices`, the actual prices. Only the tuning
    period is seen here; the chosen pair is then applied unchanged to later data.
    Returns (low, high, net revenue on the tuning data, (low_pct, high_pct)).
    """
    decide = prices if decision_prices is None else decision_prices
    flat = decide.ravel()
    best = None
    percentiles = sorted(percentiles)
    for i, lp in enumerate(percentiles):
        low = float(np.nanpercentile(flat, lp))
        for hp in percentiles[i + 1:]:
            high = float(np.nanpercentile(flat, hp))
            net = sum(settle(threshold_schedule(seen, low, high, battery), day, battery)["net_revenue"]
                      for seen, day in zip(decide, prices, strict=False))
            if best is None or net > best[2]:
                best = (low, high, net, (lp, hp))
    return best
