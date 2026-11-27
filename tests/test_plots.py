"""Checks on the figures (plots.py), with matplotlib kept off any display."""
import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from battery_dispatch import plots  # noqa: E402
from battery_dispatch.battery import Battery  # noqa: E402
from battery_dispatch.model import Schedule  # noqa: E402


def test_sample_week_handles_short_and_empty_periods(tmp_path):
    b = Battery()
    prices = pd.DataFrame(np.full((3, 24), 30.0), index=pd.date_range("2024-12-29", periods=3))
    idle = [Schedule(np.zeros(24), np.zeros(24), np.full(24, b.s_start))] * 3
    assert plots.sample_week(prices, {"persistence": idle}, prices.index[1], tmp_path / "a.png", "t")
    assert (tmp_path / "a.png").exists()
    assert not plots.sample_week(prices, {"persistence": idle}, pd.Timestamp("2025-01-06"), tmp_path / "b.png", "t")
