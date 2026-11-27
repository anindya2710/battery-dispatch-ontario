"""Checks on the pre-dispatch data and the fair comparison (rolling.py)."""
from pathlib import Path

import numpy as np
import pytest

from battery_dispatch.battery import Battery
from battery_dispatch.data import load_ieso_predispatch
from battery_dispatch.model import DailyDispatchModel
from battery_dispatch.rolling import (
    ONE_HOUR_AHEAD,
    THREE_HOURS_AHEAD,
    fill_with_persistence,
    remove_bias,
    rolling_optimizer,
)
from battery_dispatch.simulate import check_feasible, settle
from battery_dispatch.strategies import tune_threshold

FIXTURE = Path(__file__).parent / "fixtures" / "ieso_sample.csv"


def test_loader_reads_the_three_predispatch_columns():
    pd_tables = load_ieso_predispatch(FIXTURE)
    # first data row: 2024-01-01, Hour 1, HOEP 46.42, then 34.68, 33.80, 27.83
    assert pd_tables[1].iloc[0, 0] == pytest.approx(34.68)
    assert pd_tables[2].iloc[0, 0] == pytest.approx(33.80)
    assert pd_tables[3].iloc[0, 0] == pytest.approx(27.83)
    assert all(t.shape == (2, 24) for t in pd_tables.values())


def test_loader_returns_none_without_predispatch_columns(tmp_path):
    rows = ["Date,Hour,HOEP"] + [f"2024-01-01,{h},{20 + h}" for h in range(1, 25)]
    path = tmp_path / "hoep_only.csv"
    path.write_text("\n".join(rows) + "\n")
    assert load_ieso_predispatch(path) is None


def test_missing_forecasts_become_yesterdays_price():
    actual = np.array([[10.0, 20.0], [30.0, 40.0]])
    forecast = np.array([[11.0, 21.0], [np.nan, 41.0]])
    filled = fill_with_persistence(forecast, actual)
    assert filled[1, 0] == 10.0                 # yesterday, same hour
    assert filled[1, 1] == 41.0                 # a real forecast is left alone


def test_rolling_with_perfect_forecasts_matches_perfect_foresight():
    # Two identical days and pre-dispatch prices equal to the actual ones: every hour the
    # optimizer sees the true prices for the rest of the day, so re-planning hourly must
    # earn exactly what one plan made with perfect foresight earns.
    b = Battery()
    day = np.random.default_rng(4).normal(35, 25, size=24)
    actual = np.vstack([day, day])
    exact = {k: actual.copy() for k in (1, 2, 3)}
    rolled = rolling_optimizer(actual, exact, b)
    best = DailyDispatchModel(b).solve(day)
    assert settle(rolled[1], day, b)["net_revenue"] == pytest.approx(settle(best, day, b)["net_revenue"], abs=1e-4)
    assert rolled[0].charge.sum() == 0 and rolled[0].discharge.sum() == 0      # day 0 has no yesterday


def test_rolling_schedules_are_feasible_with_bad_forecasts():
    # Forecasts that are badly wrong must still give schedules the battery can follow.
    b = Battery()
    rng = np.random.default_rng(8)
    actual = rng.normal(35, 40, size=(4, 24))
    noisy = {k: actual + rng.normal(0, 60, size=actual.shape) for k in (1, 2, 3)}
    for schedule in rolling_optimizer(actual, noisy, b):
        check_feasible(schedule, b)


def test_tune_threshold_defaults_decision_prices_to_actual_prices():
    b = Battery()
    prices = np.random.default_rng(2).normal(35, 25, size=(6, 24))
    assert tune_threshold(prices, b) == tune_threshold(prices, b, decision_prices=prices)


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
    for x, y in zip(a, c, strict=False):
        assert x.charge == pytest.approx(y.charge) and x.discharge == pytest.approx(y.discharge)


def test_rolling_optimizer_defaults_to_one_hour_ahead():
    b = Battery()
    rng = np.random.default_rng(5)
    actual = rng.normal(35, 25, size=(3, 24))
    pdx = {k: actual + rng.normal(0, 10, size=actual.shape) for k in (1, 2, 3)}
    default = rolling_optimizer(actual, pdx, b)
    explicit = rolling_optimizer(actual, pdx, b, ONE_HOUR_AHEAD)
    for x, y in zip(default, explicit, strict=False):
        assert x.charge == pytest.approx(y.charge) and x.discharge == pytest.approx(y.discharge)
