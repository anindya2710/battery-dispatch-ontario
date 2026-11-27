"""Each module checked against small cases worked out by hand.

The default battery used throughout: 1 MW, 4 MWh, 88% round trip, so each way
eta = sqrt(0.88) = 0.938083; state of charge between 0.4 and 3.6 MWh, so 3.2 MWh
is usable; each day starts and ends at 0.4 MWh.
"""
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest

from battery_dispatch.battery import Battery
from battery_dispatch.data import load_ieso_hoep
from battery_dispatch.evaluate import paired_gap_interval, summarize, value_of_information
from battery_dispatch.model import DailyDispatchModel, Schedule
from battery_dispatch.simulate import check_feasible, settle, threshold_schedule
from battery_dispatch.strategies import tune_threshold

FIXTURE = Path(__file__).parent / "fixtures" / "ieso_sample.csv"
ETA = sqrt(0.88)
DEFAULT_BATTERY = Battery()


# battery.py ---------------------------------------------------------------

def test_battery_numbers():
    b = Battery()
    assert b.eta_c == pytest.approx(0.938083, abs=1e-6)
    assert b.eta_c * b.eta_d == pytest.approx(0.88)
    assert (b.s_min, b.s_max, b.s_start) == pytest.approx((0.4, 3.6, 0.4))
    assert b.usable_mwh == pytest.approx(3.2)


# simulate.py: settlement ---------------------------------------------------

def test_settle_by_hand():
    # Buy 1 MWh at $20, sell 0.5 MWh at $50, wear $10 per MWh of throughput.
    # Market: 0.5 * 50 - 1 * 20 = 5.  Wear: 10 * 1.5 = 15.  Net: -10.
    # Cycles: 0.5 MWh delivered takes 0.5 / 0.938083 = 0.533002 MWh out of storage,
    # which is 0.533002 / 3.2 = 0.166563 of the usable capacity.
    b = Battery(degradation_cost=10)
    s = Schedule(charge=np.array([1.0, 0.0]), discharge=np.array([0.0, 0.5]), soc=np.zeros(2))
    r = settle(s, [20, 50], b)
    assert r["market_revenue"] == pytest.approx(5.0)
    assert r["degradation_cost"] == pytest.approx(15.0)
    assert r["net_revenue"] == pytest.approx(-10.0)
    assert (r["charged_mwh"], r["discharged_mwh"]) == pytest.approx((1.0, 0.5))
    assert r["cycles"] == pytest.approx(0.166563, abs=1e-6)


# simulate.py: physical limits ----------------------------------------------

def _schedule(charge, discharge, battery=DEFAULT_BATTERY):
    """A schedule whose reported state of charge is the one its charge and discharge imply."""
    c, d = np.asarray(charge, float), np.asarray(discharge, float)
    return Schedule(c, d, battery.s_start + np.cumsum(battery.eta_c * c - d / battery.eta_d))


def test_check_feasible_accepts_an_idle_day():
    check_feasible(_schedule([0] * 24, [0] * 24), Battery())


@pytest.mark.parametrize("charge, discharge", [
    ([1.2, 0.0], [0.0, 1.0]),                 # 1.2 MW is above the 1 MW limit
    ([-0.1, 0.0], [0.0, 0.0]),                # negative charging
    ([1, 1, 1, 1], [0, 0, 0, 0]),             # 0.4 + 4 x 0.938 = 4.15 MWh, above 3.6
    ([0.0, 0.0], [0.5, 0.0]),                 # discharging an empty (0.4 MWh) battery goes below 0.4
    ([1.0, 0.0], [0.0, 0.0]),                 # charged but never emptied: day ends at 1.34, not 0.4
])
def test_check_feasible_refuses_each_kind_of_violation(charge, discharge):
    with pytest.raises(ValueError):
        check_feasible(_schedule(charge, discharge), Battery())


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


# model.py ------------------------------------------------------------------

def test_power_limit_binds_in_a_two_hour_day():
    # Hour 1 at $10, hour 2 at $100. Charging c stores 0.938c; ending at the start
    # level means selling d = 0.938 x 0.938 c = 0.88c. The 1 MW limit caps c at 1,
    # so buy 1, sell 0.88, profit 0.88 x 100 - 1 x 10 = 78.
    b = Battery()
    s = DailyDispatchModel(b, hours=2).solve([10, 100])
    assert s.charge == pytest.approx([1.0, 0.0], abs=1e-6)
    assert s.discharge == pytest.approx([0.0, 0.88], abs=1e-6)
    assert s.soc == pytest.approx([0.4 + ETA, 0.4], abs=1e-6)
    assert settle(s, [10, 100], b)["net_revenue"] == pytest.approx(78.0, abs=1e-5)


def test_model_objective_equals_settled_revenue():
    # The optimizer's own objective and the settlement must agree to the cent.
    b = Battery(degradation_cost=7)
    model = DailyDispatchModel(b)
    prices = np.random.default_rng(11).normal(35, 30, size=24)
    s = model.solve(prices)
    assert pyo.value(model.model.profit) == pytest.approx(settle(s, prices, b)["net_revenue"], abs=1e-4)


# simulate.py: the threshold rule ---------------------------------------------

def test_rule_is_forced_to_sell_before_midnight():
    # Only hour 22 is cheap (low = 20) and no hour is dear (high = 90).
    # Hour 22: one hour is left to empty the battery, so it may store at most
    # 1 / 0.938 = 1.066 MWh; full power (1 MW) stores 0.938, which fits.
    # Hour 23: the day must end at 0.4, so it sells 0.938 x 0.938 = 0.88 MWh at $50,
    # even though $50 is below the selling threshold. Net: 0.88 x 50 - 1 x 10 = 34.
    b = Battery()
    day = [50.0] * 24
    day[22] = 10.0
    s = threshold_schedule(day, low=20, high=90, battery=b)
    assert s.charge[22] == pytest.approx(1.0)
    assert s.discharge[23] == pytest.approx(0.88)
    assert s.charge.sum() == pytest.approx(1.0) and s.discharge.sum() == pytest.approx(0.88)
    assert settle(s, day, b)["net_revenue"] == pytest.approx(34.0)


def test_rule_does_not_charge_in_the_last_hour():
    # Nothing could be sold afterwards, so buying in hour 23 is not allowed.
    b = Battery()
    day = [50.0] * 23 + [10.0]
    s = threshold_schedule(day, low=20, high=90, battery=b)
    assert s.charge.sum() == 0 and s.discharge.sum() == 0


def test_rule_stops_charging_at_the_upper_limit():
    # Five cheap hours: 3.2 MWh of room takes 3.2 / 0.938 = 3.411 MWh from the grid,
    # i.e. three full hours and 0.411 of the fourth, then nothing in the fifth.
    b = Battery()
    day = [10.0] * 5 + [50.0] * 15 + [100.0] * 4
    s = threshold_schedule(day, low=20, high=90, battery=b)
    assert s.charge[:5] == pytest.approx([1, 1, 1, 3.2 / ETA - 3, 0], abs=1e-9)
    assert s.soc[4] == pytest.approx(3.6)


def test_tuning_finds_the_obvious_thresholds():
    # Two identical days: cheap at $10 for 4 hours, dear at $100 for 4 hours.
    # The best the rule can do each day is one full cycle: sell 3.2 x 0.938 = 3.002 MWh
    # at $100 and buy 3.2 / 0.938 = 3.411 MWh at $10, i.e. 300.19 - 34.11 = 266.07.
    b = Battery()
    day = [10.0] * 4 + [50.0] * 16 + [100.0] * 4
    low, high, net, _ = tune_threshold(np.array([day, day]), b)
    one_day = 100 * 3.2 * ETA - 10 * 3.2 / ETA
    assert net == pytest.approx(2 * one_day)
    assert low < 50 and high > 50


# data.py -------------------------------------------------------------------

def test_loader_reads_thousands_separators(tmp_path):
    # A $1,234.56 spike written with a comma inside quotes, as spreadsheets do.
    lines = FIXTURE.read_text().splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("2024-01-01,5,"))
    parts = lines[first].split(",")
    lines[first] = ",".join(parts[:2] + ['"1,234.56"'] + parts[3:])
    path = tmp_path / "spike.csv"
    path.write_text("\n".join(lines) + "\n")
    assert load_ieso_hoep(path).iloc[0, 4] == pytest.approx(1234.56)


def test_loader_refuses_duplicate_hours(tmp_path):
    lines = FIXTURE.read_text().splitlines()
    dup = next(line for line in lines if line.startswith("2024-01-01,5,"))
    path = tmp_path / "dup.csv"
    path.write_text("\n".join(lines + [dup]) + "\n")
    with pytest.raises(ValueError):
        load_ieso_hoep(path)


# evaluate.py ---------------------------------------------------------------

def test_summary_by_hand():
    # Four days. "perfect foresight" earns 10, -5, 20, 0 (25 in total, 3 cycles, 1 losing day);
    # "persistence" earns 5 every day (20 in total) and never cycles.
    # $/kW-year for 25 over 4 days on a 1 MW (1,000 kW) battery: 25 / 1000 x 365 / 4 = 2.28125.
    dates = pd.date_range("2024-07-01", periods=4)
    rows = [{"date": d, "strategy": "perfect foresight", "market_revenue": v, "degradation_cost": 0.0,
             "net_revenue": v, "cycles": c} for d, v, c in zip(dates, [10, -5, 20, 0], [1, 1, 1, 0], strict=False)]
    rows += [{"date": d, "strategy": "persistence", "market_revenue": 5.0, "degradation_cost": 0.0,
              "net_revenue": 5.0, "cycles": 0.0} for d in dates]
    s = summarize(pd.DataFrame(rows), Battery())
    pf, ps = s.loc["perfect foresight"], s.loc["persistence"]
    assert pf.net_revenue == 25 and ps.net_revenue == 20
    assert pf.net_per_kw_year == pytest.approx(2.28125)
    assert pf.net_per_cycle == pytest.approx(25 / 3)
    assert np.isnan(ps.net_per_cycle)                 # no cycles, no $/cycle
    assert (pf.losing_days, ps.losing_days) == (1, 0)
    assert ps.pct_of_reference == pytest.approx(80)
    assert value_of_information(s) == pytest.approx(5)


def _daily(gaps):
    dates = pd.date_range("2024-07-01", periods=len(gaps))
    a = [{"date": d, "strategy": "a", "net_revenue": 10.0 + g} for d, g in zip(dates, gaps, strict=False)]
    b = [{"date": d, "strategy": "b", "net_revenue": 10.0} for d in dates]
    return pd.DataFrame(a + b)


def test_gap_interval_collapses_when_every_day_is_the_same():
    # If a beats b by exactly $3 every day for 30 days, every resample totals $90.
    r = paired_gap_interval(_daily([3.0] * 30), "a", "b")
    assert r["gap"] == pytest.approx(90)
    assert (r["low"], r["high"]) == pytest.approx((90, 90))
    assert (r["days_a_ahead"], r["days_b_ahead"]) == (30, 0)
    assert r["share_resamples_a_ahead"] == 1.0


def test_gap_interval_straddles_zero_for_noise():
    # Gaps of +5 and -5 in equal numbers: total 0, and the interval must include 0.
    r = paired_gap_interval(_daily([5.0, -5.0] * 50), "a", "b")
    assert r["gap"] == pytest.approx(0)
    assert r["low"] < 0 < r["high"]
    assert (r["days_a_ahead"], r["days_b_ahead"]) == (50, 50)
