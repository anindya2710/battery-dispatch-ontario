"""Log a backtest to MLflow, so that runs can be compared over time.

Each call of `run.py --mlflow` makes one parent run, which holds what the whole
backtest shares: the price file (with a fingerprint of its contents), the
battery, the tuning period, the chosen thresholds, the headline comparisons
with their bootstrap intervals, and the summary, daily results and figures.

Under it are child runs, one per strategy and period, and one per wear cost if
the sweep was run. Their metrics have the same names, so the MLflow page can
sort, compare and plot strategies side by side, and a later backtest (a new
year, a different battery, a changed strategy) can be compared with this one.

MLflow writes to a local database (mlflow.db) by default; nothing leaves the
machine. `mlflow ui` then opens the comparison page in a browser.
"""
import hashlib
import math
from dataclasses import asdict
from pathlib import Path

import mlflow
import pandas as pd

from .battery import Battery

STRATEGY_METRICS = ["net_revenue", "market_revenue", "degradation_cost", "cycles", "net_per_kw_year",
                    "net_per_cycle", "losing_days", "pct_of_reference", "days"]


def file_fingerprint(path) -> str:
    """First 12 characters of the file's SHA-256, so a run records exactly which data it used."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def _finite(values: dict) -> dict:
    """MLflow metrics must be numbers; drop anything that is missing or not finite."""
    out = {}
    for k, v in values.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out[k] = f
    return out


def _experiment_id(name: str, artifact_location=None) -> str:
    exp = mlflow.get_experiment_by_name(name)
    if exp is not None:
        return exp.experiment_id
    # Mark it as model development, so the MLflow page opens on the runs table
    # rather than its pages for language-model traces.
    return mlflow.create_experiment(name, artifact_location=artifact_location,
                                    tags={"mlflow.experimentKind": "custom_model_development"})


def log_backtest(*, battery: Battery, data: dict, settings: dict, summaries: dict, comparisons: dict,
                 sweep: list = None, artifacts: list = (), tracking_uri: str = None,
                 experiment: str = "battery-dispatch-ontario", run_name: str = None,
                 artifact_location: str = None) -> str:
    """Log one backtest and return the parent run's id.

    data: facts about the price data (source, fingerprint, days, hours, mean price, ...)
    settings: the choices made for this backtest (wear cost, tuning period, thresholds, ...)
    summaries: {"whole period": DataFrame, "test period": DataFrame}, one row per strategy
    comparisons: {name: interval dict from evaluate.paired_gap_interval}
    sweep: optional list of {"wear_cost": k, ...metrics} rows
    artifacts: files or folders to attach to the parent run
    """
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    exp_id = _experiment_id(experiment, artifact_location)
    tags = {"data_fingerprint": str(data.get("fingerprint", "")), "data_source": str(data.get("source", ""))}

    with mlflow.start_run(experiment_id=exp_id, run_name=run_name) as parent:
        mlflow.set_tags(tags)
        mlflow.log_params({f"battery_{k}": v for k, v in asdict(battery).items()})
        mlflow.log_params({f"data_{k}": v for k, v in data.items()})
        mlflow.log_params({k: v for k, v in settings.items() if not isinstance(v, (int, float)) or isinstance(v, bool)})
        mlflow.log_metrics(_finite({k: v for k, v in settings.items()
                                    if isinstance(v, (int, float)) and not isinstance(v, bool)}))
        for name, ci in comparisons.items():
            mlflow.log_metrics(_finite({f"{name}_gap": ci["gap"], f"{name}_low": ci["low"],
                                        f"{name}_high": ci["high"],
                                        f"{name}_share_resamples_ahead": ci["share_resamples_a_ahead"]}))

        for period, table in summaries.items():
            for strategy, row in table.iterrows():
                with mlflow.start_run(experiment_id=exp_id, run_name=f"{strategy} | {period}", nested=True):
                    mlflow.set_tags({**tags, "kind": "strategy"})
                    mlflow.log_params({"strategy": strategy, "period": period})
                    mlflow.log_metrics(_finite({m: row[m] for m in STRATEGY_METRICS if m in row}))

        for row in sweep or []:
            k = row["wear_cost"]
            with mlflow.start_run(experiment_id=exp_id, run_name=f"wear cost ${k:g}/MWh", nested=True):
                mlflow.set_tags({**tags, "kind": "wear-cost sweep"})
                mlflow.log_params({"wear_cost": k, "period": "whole period"})
                mlflow.log_metrics(_finite({m: v for m, v in row.items() if m != "wear_cost"}))

        for path in artifacts:
            path = Path(path)
            if path.is_dir():
                mlflow.log_artifacts(str(path), artifact_path=path.name)
            elif path.exists():
                mlflow.log_artifact(str(path))
        return parent.info.run_id


def load_runs(tracking_uri: str = None, experiment: str = "battery-dispatch-ontario") -> pd.DataFrame:
    """All runs of the experiment as a table, newest first (a convenience for notebooks)."""
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    return mlflow.search_runs(experiment_names=[experiment], order_by=["start_time DESC"])
