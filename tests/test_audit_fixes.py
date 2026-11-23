"""Checks on the fixes made after the outside review of November 2026."""
import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest

from battery_dispatch import plots
from battery_dispatch.battery import Battery
from battery_dispatch.evaluate import daily_results
from battery_dispatch.model import DailyDispatchModel, Schedule
from battery_dispatch.rolling import (ONE_HOUR_AHEAD, THREE_HOURS_AHEAD, fill_with_persistence, remove_bias,
                                      rolling_optimizer)
from battery_dispatch.simulate import check_feasible, settle, threshold_schedule
from battery_dispatch.strategies import tune_threshold

# Two days where the best low threshold lies above the 45th percentile, where the
# first version of the search stopped.
TWO_DAYS = np.array([
    [49, 43, 36, 30, 25, 40, 34, 33, 38, 40, 22, 47, 24, 55, 45, 36, 51, 40, 106, 101, 109, 108, 33, 35],
    [43, 38, 22, 56, 23, 23, 30, 45, 54, 38, 37, 41, 37, 41, 31, 44, 43, 21, 99, 101, 104, 93, 29, 32],
], dtype=float)


def test_tuning_searches_above_the_old_limit():
    b = Battery()
    _, _, net, (lp, hp) = tune_threshold(TWO_DAYS, b)
    old_best = max(tune_threshold(TWO_DAYS, b, percentiles=[lo, hi])[2]
                   for lo in range(5, 50, 5) for hi in range(55, 100, 5))
    assert lp > 45 and lp < hp
    assert net > old_best


def test_tuning_only_tries_low_below_high():
    seen = []
    b = Battery()
    import battery_dispatch.strategies as S
    original = S.threshold_schedule
    try:
        S.threshold_schedule = lambda p, low, high, battery: (seen.append((low, high)), original(p, low, high, battery))[1]
        tune_threshold(TWO_DAYS, b, percentiles=[10, 50, 90])
    finally:
        S.threshold_schedule = original
    pairs = sorted(set(seen))
    assert len(pairs) == 3 and all(low < high for low, high in pairs)


def test_missing_forecast_on_day_zero_is_left_missing():
    actual = np.array([[10.0, 20.0], [30.0, 40.0]])
    forecast = np.array([[np.nan, 21.0], [np.nan, 41.0]])
    filled = fill_with_persistence(forecast, actual)
    assert np.isnan(filled[0, 0])               # no yesterday, and today's actual would be a leak
    assert filled[1, 0] == 10.0                 # yesterday, same hour


def test_bias_removal_uses_only_earlier_days():
    actual = np.full((4, 3), 50.0)
    forecast = actual + 5.0
    forecast[2] += 95.0                         # day 2 is off by 100
    out = remove_bias(forecast, actual)
    assert out[0] == pytest.approx(forecast[0])                      # no history yet
    assert out[1] == pytest.approx(actual[1])                        # bias from day 0: 5
    assert out[2] == pytest.approx(forecast[2] - 5.0)                # days 0-1 only, not day 2 itself
    assert out[3] == pytest.approx(forecast[3] - (5 + 5 + 100) / 3)  # days 0-2


def test_three_hour_timing_ignores_the_later_forecasts():
    # With ahead=(3,), the 1- and 2-hour-ahead prices must make no difference.
    b = Battery()
    rng = np.random.default_rng(3)
    actual = rng.normal(35, 25, size=(3, 24))
    pd3 = actual + rng.normal(0, 10, size=actual.shape)
    a = rolling_optimizer(actual, {1: actual * 0, 2: actual * 0, 3: pd3}, b, THREE_HOURS_AHEAD)
    c = rolling_optimizer(actual, {1: actual + 500, 2: actual - 500, 3: pd3}, b, THREE_HOURS_AHEAD)
    for x, y in zip(a, c):
        assert x.charge == pytest.approx(y.charge) and x.discharge == pytest.approx(y.discharge)


def test_one_hour_timing_is_unchanged_by_the_new_option():
    b = Battery()
    rng = np.random.default_rng(5)
    actual = rng.normal(35, 25, size=(3, 24))
    pdx = {k: actual + rng.normal(0, 10, size=actual.shape) for k in (1, 2, 3)}
    default = rolling_optimizer(actual, pdx, b)
    explicit = rolling_optimizer(actual, pdx, b, ONE_HOUR_AHEAD)
    for x, y in zip(default, explicit):
        assert x.charge == pytest.approx(y.charge) and x.discharge == pytest.approx(y.discharge)


def test_rule_uses_the_whole_battery_when_the_day_starts_above_the_minimum():
    # Start at 50%. A high price first, then cheap hours: the rule may now discharge
    # below the starting level, as long as it can refill before the day ends.
    b = Battery(soc_start_frac=0.5)
    day = [100.0] * 3 + [50.0] * 15 + [10.0] * 6
    s = threshold_schedule(day, low=20, high=90, battery=b)
    check_feasible(s, b)
    assert s.soc.min() < b.s_start - 1                  # went well below the start level
    assert s.soc[-1] == pytest.approx(b.s_start)
    assert settle(s, day, b)["net_revenue"] > 0


def test_rule_unchanged_when_the_day_starts_at_the_minimum():
    # The default battery starts at its lower limit, so the new branch never acts:
    # same schedules as a rule that can never discharge below the start level.
    b = Battery()
    rng = np.random.default_rng(9)
    for _ in range(20):
        day = rng.normal(35, 30, size=24)
        s = threshold_schedule(day, 20, 50, b)
        assert s.soc.min() >= b.s_start - 1e-9
        check_feasible(s, b)


def test_feasibility_check_refuses_simultaneous_trades_and_wrong_soc():
    b = Battery()
    c, d = np.zeros(24), np.zeros(24)
    c[0], d[0], d[1] = 0.5, 0.2, 0.0
    d[1] = 0.5 * b.eta_c * b.eta_d - 0.2       # end the day where it began
    soc = b.s_start + np.cumsum(b.eta_c * c - d / b.eta_d)
    with pytest.raises(ValueError, match="same hour"):
        check_feasible(Schedule(c, d, soc), b)
    check_feasible(Schedule(c, d, soc), b, allow_simultaneous=True)
    idle = np.zeros(24)
    with pytest.raises(ValueError, match="reported state of charge"):
        check_feasible(Schedule(idle, idle, np.zeros(24)), b)


def test_milp_reaches_its_lp_relaxation_when_prices_are_positive():
    # With no negative prices the binaries cannot bind, so solved to optimality the
    # two objectives must agree to rounding.
    b = Battery()
    milp, lp = DailyDispatchModel(b), DailyDispatchModel(b, binary=False)
    rng = np.random.default_rng(1)
    for _ in range(5):
        p = np.abs(rng.normal(40, 60, size=24)) + 1
        milp.solve(p), lp.solve(p)
        assert pyo.value(milp.model.profit) == pytest.approx(pyo.value(lp.model.profit), abs=1e-6)


def test_daily_results_refuses_a_length_mismatch():
    prices = pd.DataFrame(np.full((2, 24), 30.0), index=pd.date_range("2024-07-01", periods=2))
    one = [Schedule(np.zeros(24), np.zeros(24), np.full(24, Battery().s_start))]
    with pytest.raises(ValueError):
        daily_results("x", one, prices, Battery())


def test_sample_week_handles_short_and_empty_periods(tmp_path):
    b = Battery()
    prices = pd.DataFrame(np.full((3, 24), 30.0), index=pd.date_range("2024-12-29", periods=3))
    idle = [Schedule(np.zeros(24), np.zeros(24), np.full(24, b.s_start))] * 3
    assert plots.sample_week(prices, {"persistence": idle}, prices.index[1], tmp_path / "a.png", "t")
    assert (tmp_path / "a.png").exists()
    assert not plots.sample_week(prices, {"persistence": idle}, pd.Timestamp("2025-01-06"), tmp_path / "b.png", "t")
