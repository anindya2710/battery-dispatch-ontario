"""Scoring: daily results per strategy, and the summary numbers."""
import numpy as np
import pandas as pd

from .battery import Battery
from .simulate import check_feasible, settle


def daily_results(name: str, schedules: list, prices: pd.DataFrame, battery: Battery) -> pd.DataFrame:
    """Settle every day's schedule at that day's actual prices."""
    rows = []
    for date, day_prices, schedule in zip(prices.index, prices.to_numpy(), schedules):
        check_feasible(schedule, battery)
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


def value_of_optimization(summary: pd.DataFrame) -> float:
    """What optimizing on a naive forecast adds over the tuned threshold rule."""
    return float(summary.loc["persistence", "net_revenue"] - summary.loc["threshold rule", "net_revenue"])
