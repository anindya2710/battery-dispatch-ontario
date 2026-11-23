"""Checks that a backtest is logged to MLflow as intended (tracking.py).

Each test writes to its own temporary database and folder, so nothing is left behind.
"""
import numpy as np
import pandas as pd
import pytest

mlflow = pytest.importorskip("mlflow")

from battery_dispatch.battery import Battery
from battery_dispatch.tracking import file_fingerprint, load_runs, log_backtest


def _summary():
    return pd.DataFrame(
        {"net_revenue": [100.0, 60.0], "market_revenue": [100.0, 60.0], "degradation_cost": [0.0, 0.0],
         "cycles": [10.0, 0.0], "days": [4, 4], "net_per_kw_year": [9.125, 5.475],
         "net_per_cycle": [10.0, np.nan], "losing_days": [0, 1], "pct_of_reference": [100.0, 60.0]},
        index=pd.Index(["perfect foresight", "persistence"], name="strategy"))


def _log(tmp_path, **extra):
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    summary = tmp_path / "summary.md"
    summary.write_text("# test\n")
    run_id = log_backtest(
        battery=Battery(degradation_cost=5), tracking_uri=uri, experiment="test", run_name="unit test",
        artifact_location=(tmp_path / "artifacts").as_uri(),
        data={"source": "made-up", "fingerprint": "abc123", "days": 4},
        settings={"tune_until": "2024-06-30", "rule_low": 20.5, "predispatch_comparison": False},
        summaries={"whole period": _summary()},
        comparisons={"value_of_information_test": {"gap": 40.0, "low": 10.0, "high": 70.0,
                                                   "share_resamples_a_ahead": 0.99}},
        artifacts=[summary], **extra)
    return uri, run_id


def test_parent_run_holds_the_shared_facts(tmp_path):
    uri, run_id = _log(tmp_path)
    run = mlflow.tracking.MlflowClient(uri).get_run(run_id)
    assert run.data.params["battery_degradation_cost"] == "5"
    assert run.data.params["data_fingerprint"] == "abc123"
    assert run.data.params["tune_until"] == "2024-06-30"
    assert run.data.metrics["rule_low"] == pytest.approx(20.5)
    assert run.data.metrics["value_of_information_test_gap"] == pytest.approx(40)
    assert run.data.metrics["value_of_information_test_low"] == pytest.approx(10)
    names = [a.path for a in mlflow.tracking.MlflowClient(uri).list_artifacts(run_id)]
    assert "summary.md" in names


def test_one_child_run_per_strategy_with_comparable_metrics(tmp_path):
    uri, run_id = _log(tmp_path)
    runs = load_runs(uri, "test")
    children = runs[runs["tags.mlflow.parentRunId"] == run_id].set_index("params.strategy")
    assert set(children.index) == {"perfect foresight", "persistence"}
    assert children.loc["perfect foresight", "metrics.net_revenue"] == pytest.approx(100)
    assert children.loc["persistence", "metrics.losing_days"] == 1
    # no cycles means no $/cycle: the missing value is left out, not logged as a number
    assert np.isnan(children.loc["persistence", "metrics.net_per_cycle"])


def test_wear_cost_sweep_gets_its_own_runs(tmp_path):
    sweep = [{"wear_cost": 0.0, "pf_net_revenue": 100.0}, {"wear_cost": 10.0, "pf_net_revenue": 70.0}]
    uri, run_id = _log(tmp_path, sweep=sweep)
    runs = load_runs(uri, "test")
    swept = runs[runs["tags.kind"] == "wear-cost sweep"].set_index("params.wear_cost")
    assert swept.loc["10.0", "metrics.pf_net_revenue"] == pytest.approx(70)


def test_fingerprint_changes_when_the_data_changes(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("Date,Hour,HOEP\n2024-01-01,1,10\n")
    b.write_text("Date,Hour,HOEP\n2024-01-01,1,11\n")
    assert file_fingerprint(a) != file_fingerprint(b)
    assert len(file_fingerprint(a)) == 12
