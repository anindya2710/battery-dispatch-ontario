"""Checks on the optimization model against answers worked out by hand."""
import numpy as np
import pytest

from battery_dispatch.battery import Battery
from battery_dispatch.model import DailyDispatchModel
from battery_dispatch.simulate import check_feasible, settle


def test_toy_problem_matches_lesson_one():
    # Buy up to 1 MWh at $20, lose 10% in storage, sell at $50: buy 1.0, sell 0.9, profit 25.
    # The 1 MW power limit over one hour is what caps the purchase at 1 MWh.
    toy = Battery(power_mw=1, energy_mwh=1, round_trip_eff=0.9, soc_min_frac=0, soc_max_frac=1, soc_start_frac=0)
    s = DailyDispatchModel(toy, hours=2).solve([20, 50])
    assert s.charge.sum() == pytest.approx(1.0, abs=1e-6)
    assert s.discharge.sum() == pytest.approx(0.9, abs=1e-6)
    assert settle(s, [20, 50], toy)["net_revenue"] == pytest.approx(25.0, abs=1e-6)


def test_flat_prices_mean_no_trade():
    b = Battery()
    s = DailyDispatchModel(b).solve([40.0] * 24)
    assert s.charge.sum() == pytest.approx(0, abs=1e-8)
    assert s.discharge.sum() == pytest.approx(0, abs=1e-8)


def test_one_full_cycle_by_hand():
    # Four cheap hours then four dear hours: fill the usable 3.2 MWh, then empty it.
    b = Battery()
    prices = [10] * 4 + [100] * 4
    s = DailyDispatchModel(b, hours=8).solve(prices)
    bought = b.usable_mwh / b.eta_c          # grid energy needed to store 3.2 MWh
    sold = b.usable_mwh * b.eta_d            # grid energy delivered from 3.2 MWh
    assert s.charge.sum() == pytest.approx(bought, abs=1e-6)
    assert s.discharge.sum() == pytest.approx(sold, abs=1e-6)
    assert settle(s, prices, b)["net_revenue"] == pytest.approx(100 * sold - 10 * bought, abs=1e-5)
    assert settle(s, prices, b)["cycles"] == pytest.approx(1.0, abs=1e-6)


def test_schedule_respects_limits_and_never_charges_and_discharges_together():
    b = Battery()
    rng = np.random.default_rng(1)
    model = DailyDispatchModel(b)
    for _ in range(20):
        prices = rng.normal(30, 40, size=24)          # includes negative prices
        s = model.solve(prices)
        check_feasible(s, b)
        assert np.all(np.minimum(s.charge, s.discharge) < 1e-7)


def test_small_spread_is_not_worth_the_losses():
    # Selling at 110% of the buying price loses money at 88% round-trip efficiency.
    b = Battery()
    s = DailyDispatchModel(b, hours=2).solve([100, 110])
    assert s.charge.sum() == pytest.approx(0, abs=1e-8)


def test_degradation_cost_stops_marginal_trades():
    prices = [40] * 4 + [60] * 4
    free = DailyDispatchModel(Battery(), hours=8).solve(prices)
    costly = DailyDispatchModel(Battery(degradation_cost=20), hours=8).solve(prices)
    assert free.discharge.sum() > 1
    assert costly.discharge.sum() == pytest.approx(0, abs=1e-8)


def test_lp_relaxation_never_does_worse_than_milp():
    b = Battery()
    prices = np.array([-50, -40, 10, 20, 80, 90, -30, 60] * 3, dtype=float)
    milp = settle(DailyDispatchModel(b, binary=True).solve(prices), prices, b)["net_revenue"]
    lp = settle(DailyDispatchModel(b, binary=False).solve(prices), prices, b)["net_revenue"]
    assert lp >= milp - 1e-6
