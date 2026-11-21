r"""Price data.

The real input is the IESO "Yearly HOEP OR Predispatch Report", for example
PUB_PriceHOEPPredispOR_2024.csv. Its first lines look like this:

    \\Yearly HOEP OR Predispatch Report,,,,,,,,
    \\Created at 2025-01-31 08:02:20,,,,,,,,
    \\For 2024,,,,,,,,
    Date,Hour,HOEP,Hour 1 Predispatch,Hour 2 Predispatch,Hour 3 Predispatch,OR 10 Min Sync,...
    2024-01-01,1,46.42,34.68,33.80,27.83,1.26,0.17,0.17

Things to know about it:
  * Lines starting with a backslash are comments.
  * Hour runs 1-24 and is "hour ending": Hour 1 covers 00:00-01:00.
  * Times are Eastern Standard Time all year (no daylight saving), so every day
    has exactly 24 hours.
  * HOEP is in $/MWh and can be negative.
"""
from pathlib import Path

import numpy as np
import pandas as pd

HOURS = 24


PREDISPATCH_COLUMNS = {1: "Hour 1 Predispatch", 2: "Hour 2 Predispatch", 3: "Hour 3 Predispatch"}


def _read_ieso(path) -> pd.DataFrame:
    raw = pd.read_csv(Path(path), comment="\\", skip_blank_lines=True)
    raw.columns = [c.strip() for c in raw.columns]
    return raw


def load_ieso_hoep(path) -> pd.DataFrame:
    """Read an IESO yearly HOEP file into a table with one row per day and 24 hour columns.

    Returns a DataFrame indexed by date, columns 0..23 (hour beginning), values in $/MWh.
    Raises ValueError if any day is incomplete or any price is missing, because a
    silent gap would shift every later hour.
    """
    return _ieso_table(_read_ieso(path), "HOEP", path, allow_missing=False)


def load_ieso_predispatch(path) -> dict:
    """The pre-dispatch prices in the same file, as {1: table, 2: table, 3: table}.

    "Hour k Predispatch" for hour h is IESO's projection of the price for hour h,
    published k hours before hour h starts. So at the start of hour h a trader
    already knows the 1-hour-ahead price for h, the 2-hour-ahead price for h+1
    and the 3-hour-ahead price for h+2, all published during hour h-1.
    Returns None if the file has no pre-dispatch columns. Missing values are kept
    as NaN; the strategies fill them with the persistence forecast.
    """
    raw = _read_ieso(path)
    if not set(PREDISPATCH_COLUMNS.values()) <= set(raw.columns):
        return None
    return {k: _ieso_table(raw, col, path, allow_missing=True) for k, col in PREDISPATCH_COLUMNS.items()}


def _ieso_table(raw: pd.DataFrame, column: str, path, allow_missing: bool) -> pd.DataFrame:
    missing_cols = {"Date", "Hour", column} - set(raw.columns)
    if missing_cols:
        raise ValueError(f"{path}: expected columns Date, Hour, {column}; missing {sorted(missing_cols)}")

    df = raw[["Date", "Hour", column]].copy()
    df["Date"] = pd.to_datetime(df["Date"]).dt.date
    df["Hour"] = df["Hour"].astype(int)
    # tolerate prices written with a thousands separator, e.g. "1,234.56"
    df[column] = pd.to_numeric(df[column].astype(str).str.replace(",", "", regex=False), errors="coerce")
    if not allow_missing and df[column].isna().any():
        bad = df[df[column].isna()].head(5)
        raise ValueError(f"{path}: missing {column} values, e.g.\n{bad}")
    if df.duplicated(["Date", "Hour"]).any():
        raise ValueError(f"{path}: duplicate Date/Hour rows")

    table = df.pivot(index="Date", columns="Hour", values=column)
    expected = list(range(1, HOURS + 1))
    if list(table.columns) != expected or (not allow_missing and table.isna().any().any()):
        short = table[table.isna().any(axis=1)].index.tolist()[:5]
        raise ValueError(f"{path}: every day needs hours 1-24; incomplete days include {short}")

    all_days = pd.date_range(table.index.min(), table.index.max(), freq="D").date
    if len(all_days) != len(table):
        absent = sorted(set(all_days) - set(table.index))[:5]
        raise ValueError(f"{path}: missing whole days, e.g. {absent}")

    table.columns = range(HOURS)          # hour ending 1..24  ->  hour beginning 0..23
    table.index = pd.to_datetime(table.index)
    table.index.name = "date"
    return table.astype(float)


def describe_prices(prices: pd.DataFrame) -> dict:
    """A few facts worth printing before trusting any result."""
    values = prices.to_numpy()
    return {
        "days": int(len(prices)),
        "hours": int(values.size),
        "first_day": str(prices.index.min().date()),
        "last_day": str(prices.index.max().date()),
        "mean_price": float(values.mean()),
        "min_price": float(values.min()),
        "max_price": float(values.max()),
        "negative_hours": int((values < 0).sum()),
        "zero_hours": int((values == 0).sum()),
    }


def synthetic_prices(year: int = 2024, seed: int = 0) -> pd.DataFrame:
    """Made-up hourly prices with a daily shape, noise, spikes and a few negative hours.

    For testing the code only. Nothing computed from these prices is a result.
    """
    rng = np.random.default_rng(seed)
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    hour = np.arange(HOURS)
    shape = (
        30
        + 14 * np.exp(-0.5 * ((hour - 8) / 2.0) ** 2)      # morning peak
        + 24 * np.exp(-0.5 * ((hour - 18) / 2.5) ** 2)     # evening peak
        - 10 * np.exp(-0.5 * ((hour - 3) / 2.0) ** 2)      # overnight trough
    )
    level = 1 + 0.25 * np.sin(2 * np.pi * (np.arange(len(days)) - 15) / len(days))
    daily = rng.normal(1.0, 0.15, size=len(days))
    prices = shape[None, :] * (level * daily)[:, None] + rng.normal(0, 6, size=(len(days), HOURS))
    spikes = rng.random(prices.shape) < 0.01
    prices[spikes] += rng.exponential(120, size=spikes.sum())
    dips = rng.random(prices.shape) < 0.01
    prices[dips] -= rng.exponential(40, size=dips.sum())
    return pd.DataFrame(np.round(prices, 2), index=pd.Index(days, name="date"), columns=range(HOURS))


def synthetic_predispatch(prices: pd.DataFrame, seed: int = 1) -> dict:
    """Made-up pre-dispatch prices: the actual price plus noise that grows with the lead time.

    For testing the code only.
    """
    rng = np.random.default_rng(seed)
    return {k: prices + rng.normal(0, 4 + 3 * k, size=prices.shape) for k in (1, 2, 3)}
