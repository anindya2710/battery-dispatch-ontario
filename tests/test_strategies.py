"""Checks on the strategies, the settlement and the data loader."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from battery_dispatch.battery import Battery
from battery_dispatch.data import describe_prices, load_ieso_hoep, synthetic_prices
from battery_dispatch.evaluate import daily_results, summarize
from battery_dispatch.model import DailyDispatchModel, Schedule
from battery_dispatch.simulate import check_feasible, settle, threshold_schedule
from battery_dispatch.strategies import perfect_foresight, persistence, threshold, tune_threshold

FIXTURE = Path(__file__).parent / "fixtures" / "ieso_sample.csv"

# Two days with a high evening spike where the optimal charging threshold lies above the 50th percentile.
HIGH_SPIKE_DAYS = np.array([
    [49, 43, 36, 30, 25, 40, 34, 33, 38, 40, 22, 47, 24, 55, 45, 36, 51, 40, 106, 101, 109, 108, 33, 35],
    [43, 38, 22, 56, 23, 23, 30, 45, 54, 38, 37, 41, 37, 41, 31, 44, 43, 21, 99, 101, 104, 93, 29, 32],
], dtype=float)


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
    broken.write_text("\n".join(line for line in lines if not line.startswith("2024-01-02,7,")) + "\n")
    with pytest.raises(ValueError):
        load_ieso_hoep(broken)


def test_loader_refuses_a_missing_day(tmp_path):
    lines = FIXTURE.read_text().splitlines()
    third_day = [line.replace("2024-01-02", "2024-01-04") for line in lines if line.startswith("2024-01-02")]
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
    for day, a, c, d in zip(P, pf, ps, th, strict=False):
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


def test_tuning_searches_full_percentile_grid():
    b = Battery()
    _, _, net, (lp, hp) = tune_threshold(HIGH_SPIKE_DAYS, b)
    restricted_best = max(tune_threshold(HIGH_SPIKE_DAYS, b, percentiles=[lo, hi])[2]
                          for lo in range(5, 50, 5) for hi in range(55, 100, 5))
    assert lp > 45 and lp < hp
    assert net > restricted_best


def test_tuning_only_tries_low_below_high():
    seen = []
    b = Battery()
    import battery_dispatch.strategies as S
    original = S.threshold_schedule
    try:
        def record(p, low, high, battery):
            seen.append((low, high))
            return original(p, low, high, battery)
        S.threshold_schedule = record
        tune_threshold(HIGH_SPIKE_DAYS, b, percentiles=[10, 50, 90])
    finally:
        S.threshold_schedule = original
    pairs = sorted(set(seen))
    assert len(pairs) == 3 and all(low < high for low, high in pairs)


def test_rule_uses_the_whole_battery_when_the_day_starts_above_the_minimum():
    # Start at 50%. A high price first, then cheap hours: the rule can discharge
    # below the starting level, as long as it can refill before the day ends.
    b = Battery(soc_start_frac=0.5)
    day = [100.0] * 3 + [50.0] * 15 + [10.0] * 6
    s = threshold_schedule(day, low=20, high=90, battery=b)
    check_feasible(s, b)
    assert s.soc.min() < b.s_start - 1                  # went well below the start level
    assert s.soc[-1] == pytest.approx(b.s_start)
    assert settle(s, day, b)["net_revenue"] > 0


def test_rule_respects_floor_when_day_starts_at_minimum():
    # When the day starts at the lower SoC limit, the rule never discharges below s_start.
    b = Battery()
    rng = np.random.default_rng(9)
    for _ in range(20):
        day = rng.normal(35, 30, size=24)
        s = threshold_schedule(day, 20, 50, b)
        assert s.soc.min() >= b.s_start - 1e-9
        check_feasible(s, b)


def test_daily_results_refuses_a_length_mismatch():
    prices = pd.DataFrame(np.full((2, 24), 30.0), index=pd.date_range("2024-07-01", periods=2))
    one = [Schedule(np.zeros(24), np.zeros(24), np.full(24, Battery().s_start))]
    with pytest.raises(ValueError):
        daily_results("x", one, prices, Battery())
