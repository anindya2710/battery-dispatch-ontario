"""Checks on the strategies, the settlement and the data loader."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from battery_dispatch.battery import Battery
from battery_dispatch.data import describe_prices, load_ieso_hoep, synthetic_prices
from battery_dispatch.evaluate import daily_results, summarize
from battery_dispatch.model import DailyDispatchModel
from battery_dispatch.simulate import check_feasible, settle, threshold_schedule
from battery_dispatch.strategies import perfect_foresight, persistence, threshold, tune_threshold

FIXTURE = Path(__file__).parent / "fixtures" / "ieso_sample.csv"


@pytest.fixture(scope="module")
def prices():
    return synthetic_prices(seed=3).iloc[:40]


def test_loader_reads_ieso_layout():
    table = load_ieso_hoep(FIXTURE)
    assert table.shape == (2, 24)
    assert list(table.columns) == list(range(24))
    assert table.iloc[0, 0] == 46.42          # 2024-01-01 Hour 1 becomes hour beginning 0
    assert table.iloc[0, 7] == 27.52          # Hour 8
    assert table.iloc[1, 3] == -3.10          # negative prices are kept
    assert describe_prices(table)["negative_hours"] == 1


def test_loader_refuses_a_day_with_a_missing_hour(tmp_path):
    lines = FIXTURE.read_text().splitlines()
    broken = tmp_path / "broken.csv"
    broken.write_text("\n".join(l for l in lines if not l.startswith("2024-01-02,7,")) + "\n")
    with pytest.raises(ValueError):
        load_ieso_hoep(broken)


def test_loader_refuses_a_missing_day(tmp_path):
    lines = FIXTURE.read_text().splitlines()
    third_day = [l.replace("2024-01-02", "2024-01-04") for l in lines if l.startswith("2024-01-02")]
    gap = tmp_path / "gap.csv"
    gap.write_text("\n".join(lines + third_day) + "\n")
    with pytest.raises(ValueError):
        load_ieso_hoep(gap)


def test_threshold_rule_is_always_feasible():
    b = Battery()
    rng = np.random.default_rng(5)
    for _ in range(200):
        day = rng.normal(30, 40, size=24)
        low, high = np.sort(rng.normal(30, 30, size=2))
        check_feasible(threshold_schedule(day, low, high, b), b)


def test_threshold_rule_by_hand():
    # Cheap for four hours, dear for four hours, nothing in between.
    b = Battery()
    day = [10] * 4 + [50] * 16 + [100] * 4
    s = threshold_schedule(day, low=20, high=90, battery=b)
    assert s.charge.sum() == pytest.approx(b.usable_mwh / b.eta_c)
    assert s.discharge[20:].sum() == pytest.approx(b.usable_mwh * b.eta_d)
    assert s.discharge[:20].sum() == 0


def test_perfect_foresight_is_an_upper_bound_every_day(prices):
    b = Battery()
    P = prices.to_numpy()
    pf = perfect_foresight(P, b)
    ps = persistence(pf, b)
    low, high, _, _ = tune_threshold(P[:20], b)
    th = threshold(P, low, high, b)
    for day, a, c, d in zip(P, pf, ps, th):
        best = settle(a, day, b)["net_revenue"]
        assert best >= settle(c, day, b)["net_revenue"] - 1e-6
        assert best >= settle(d, day, b)["net_revenue"] - 1e-6
        assert best >= -1e-9                      # doing nothing is always allowed


def test_persistence_is_yesterdays_optimum(prices):
    b = Battery()
    P = prices.to_numpy()[:5]
    ps = persistence(perfect_foresight(P, b), b)
    assert ps[0].charge.sum() == 0 and ps[0].discharge.sum() == 0
    model = DailyDispatchModel(b)
    for k in range(1, 5):
        direct = model.solve(P[k - 1])
        # the optimum can be non-unique, so compare what the plan earns on its own prices
        assert settle(ps[k], P[k - 1], b)["net_revenue"] == pytest.approx(
            settle(direct, P[k - 1], b)["net_revenue"], abs=1e-6)


def test_summary_adds_up(prices):
    b = Battery(degradation_cost=5)
    pf = perfect_foresight(prices.to_numpy(), b)
    daily = daily_results("perfect foresight", pf, prices, b)
    s = summarize(daily, b)
    row = s.loc["perfect foresight"]
    assert row.net_revenue == pytest.approx(row.market_revenue - row.degradation_cost)
    assert row.days == len(prices)
    assert row.pct_of_reference == pytest.approx(100)
    assert isinstance(daily["date"].iloc[0], pd.Timestamp)
