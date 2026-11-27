"""Scoring: daily results per strategy, and the summary numbers."""
import numpy as np
import pandas as pd

from .battery import Battery
from .simulate import check_feasible, settle


def daily_results(name: str, schedules: list, prices: pd.DataFrame, battery: Battery,
                  allow_simultaneous: bool = False) -> pd.DataFrame:
    """Settle every day's schedule at that day's actual prices.

    `schedules` has one schedule per row of `prices`, in the same order.
    """
    if len(schedules) != len(prices):
        raise ValueError(f"{len(schedules)} schedules for {len(prices)} days")
    rows = []
    for date, day_prices, schedule in zip(prices.index, prices.to_numpy(), schedules, strict=False):
        check_feasible(schedule, battery, allow_simultaneous=allow_simultaneous)
        rows.append({"date": date, "strategy": name, **settle(schedule, day_prices, battery)})
    return pd.DataFrame(rows)


def summarize(daily: pd.DataFrame, battery: Battery, reference: str = "perfect foresight") -> pd.DataFrame:
    """One row per strategy over whatever days are in `daily`."""
    g = daily.groupby("strategy", sort=False)
    out = g[["market_revenue", "degradation_cost", "net_revenue", "cycles"]].sum()
    days = g["date"].nunique()
    out["days"] = days
    out["net_per_kw_year"] = out["net_revenue"] / (battery.power_mw * 1000) * 365 / days
    out["net_per_cycle"] = out["net_revenue"] / out["cycles"].replace(0, np.nan)
    out["losing_days"] = g["net_revenue"].apply(lambda s: int((s < -1e-9).sum()))
    if reference in out.index and out.loc[reference, "net_revenue"] > 0:
        out["pct_of_reference"] = 100 * out["net_revenue"] / out.loc[reference, "net_revenue"]
    else:
        out["pct_of_reference"] = np.nan
    return out


def value_of_information(summary: pd.DataFrame) -> float:
    """What a perfect forecast would add over the persistence forecast."""
    return float(summary.loc["perfect foresight", "net_revenue"] - summary.loc["persistence", "net_revenue"])


def paired_gap_interval(daily: pd.DataFrame, a: str, b: str, block_days: int = 7,
                        n_boot: int = 10_000, level: float = 0.95, seed: int = 0) -> dict:
    """How sure can we be about the total revenue gap between strategies a and b?

    The gap is taken day by day (a minus b on the same day, so both face the same
    prices), then resampled in blocks of `block_days` consecutive days, so that
    any day-to-day dependence in prices is kept inside each block (a moving-block
    bootstrap). Returns the observed total gap, the interval, and the share of
    resamples in which a came out ahead.
    """
    wide = daily.pivot(index="date", columns="strategy", values="net_revenue").sort_index()
    diff = (wide[a] - wide[b]).to_numpy()
    n = len(diff)
    if n == 0:
        raise ValueError("no days to compare")
    L = max(1, min(block_days, n))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - L + 1, size=(n_boot, int(np.ceil(n / L))))
    idx = (starts[:, :, None] + np.arange(L)[None, None, :]).reshape(n_boot, -1)[:, :n]
    totals = diff[idx].sum(axis=1)
    tail = 100 * (1 - level) / 2
    low, high = np.percentile(totals, [tail, 100 - tail])
    return {
        "gap": float(diff.sum()),
        "low": float(low),
        "high": float(high),
        "level": level,
        "days": n,
        "days_a_ahead": int((diff > 1e-9).sum()),
        "days_b_ahead": int((diff < -1e-9).sum()),
        "share_resamples_a_ahead": float((totals > 0).mean()),
    }
