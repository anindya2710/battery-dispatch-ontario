"""The strategies being compared.

perfect foresight   optimize each day knowing that day's actual prices. Nobody
                    can do this; it is the upper bound every other strategy is
                    measured against.
persistence         optimize each day on YESTERDAY's prices (the simplest
                    forecast there is), then settle at today's actual prices.
threshold rule      no optimization: charge below one price, discharge above
                    another. The two prices are tuned on the first part of the
                    data and tested on the rest.
do nothing          zero, for reference.
"""
import numpy as np

from .battery import Battery
from .model import DailyDispatchModel, Schedule
from .simulate import settle, threshold_schedule


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


def tune_threshold(prices: np.ndarray, battery: Battery,
                   low_percentiles=range(5, 50, 5), high_percentiles=range(55, 100, 5)):
    """Pick the (low, high) price pair that earned the most on `prices`.

    Candidates are percentiles of the tuning prices. Only the tuning period is
    seen here; the chosen pair is then applied unchanged to later data.
    Returns (low, high, net revenue on the tuning data, (low_pct, high_pct)).
    """
    flat = prices.ravel()
    best = None
    for lp in low_percentiles:
        low = float(np.percentile(flat, lp))
        for hp in high_percentiles:
            high = float(np.percentile(flat, hp))
            net = sum(settle(threshold_schedule(day, low, high, battery), day, battery)["net_revenue"]
                      for day in prices)
            if best is None or net > best[2]:
                best = (low, high, net, (lp, hp))
    return best
