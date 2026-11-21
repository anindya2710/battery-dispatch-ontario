"""Checks on the pre-dispatch data and the fair comparison (rolling.py)."""
from pathlib import Path

import numpy as np
import pytest

from battery_dispatch.battery import Battery
from battery_dispatch.data import load_ieso_predispatch
from battery_dispatch.model import DailyDispatchModel
from battery_dispatch.rolling import fill_with_persistence, rolling_optimizer
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


def test_tuning_on_the_actual_prices_is_unchanged_by_the_new_option():
    b = Battery()
    prices = np.random.default_rng(2).normal(35, 25, size=(6, 24))
    assert tune_threshold(prices, b) == tune_threshold(prices, b, decision_prices=prices)
