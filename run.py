"""Run the whole analysis and write results/summary.md, a daily CSV and three figures.

If the price file has IESO's pre-dispatch columns (the yearly HOEP report does), it
also runs the strategies that act on pre-dispatch prices (battery_dispatch/rolling.py).
That takes several minutes, because the optimizer re-plans every hour of the year,
once for each kind of timing and once more with the forecasts' bias removed.

    python run.py --prices data/raw/PUB_PriceHOEPPredispOR_2024.csv
    python run.py --synthetic                 # made-up prices, to check the code runs

Useful options:
    --kappa 20                  wear cost in $ per MWh charged or discharged (default 0)
    --sweep-kappa 0,10,20,50    repeat the optimizing strategies at several wear costs
    --lp-check                  compare the MILP with its LP relaxation
    --mlflow                    also log the backtest to MLflow (see battery_dispatch/tracking.py)

The first day in the file is used only as "yesterday" for the second: no strategy is
evaluated on it, and the threshold rules are not tuned on it.
"""
import argparse
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from battery_dispatch import plots
from battery_dispatch.battery import Battery
from battery_dispatch.data import (describe_prices, load_ieso_hoep, load_ieso_predispatch, synthetic_predispatch,
                                  synthetic_prices)
from battery_dispatch.evaluate import daily_results, paired_gap_interval, summarize
from battery_dispatch.rolling import (ONE_HOUR_AHEAD, THREE_HOURS_AHEAD, fill_with_persistence, remove_bias,
                                      rolling_optimizer)
from battery_dispatch.strategies import (OPT_1H, OPT_1H_UNBIASED, OPT_3H, OPT_3H_EXACT, OPT_3H_UNBIASED, PERSIST, PF,
                                         RULE, RULE_1H, RULE_3H, perfect_foresight, persistence, threshold,
                                         tune_threshold)

UNTUNED = [PF, PERSIST, OPT_1H, OPT_3H]                       # nothing fitted to the data
MAIN = [PF, PERSIST, RULE, OPT_1H, RULE_1H, OPT_3H, RULE_3H]    # the strategies compared


def money(x):
    return f"{x:,.0f}"


def dollars(x):
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def table(summary: pd.DataFrame) -> str:
    lines = ["| Strategy | Net revenue ($) | $/kW-year | Cycles | $/cycle | Losing days | % of perfect foresight |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, r in summary.iterrows():
        per_cycle = "" if pd.isna(r.net_per_cycle) else f"{r.net_per_cycle:,.1f}"
        pct = "" if pd.isna(r.pct_of_reference) else f"{r.pct_of_reference:.0f}%"
        lines.append(f"| {name} | {money(r.net_revenue)} | {r.net_per_kw_year:,.1f} | {r.cycles:,.0f} | "
                     f"{per_cycle} | {int(r.losing_days)} | {pct} |")
    return "\n".join(lines)


def gap_line(label: str, ci: dict, a_name: str, b_name: str) -> str:
    return (f"- {label}: **{dollars(ci['gap'])}**, 95% interval {dollars(ci['low'])} to {dollars(ci['high'])}. "
            f"{a_name[0].upper() + a_name[1:]} earned more on {ci['days_a_ahead']} of {ci['days']} days, {b_name} on "
            f"{ci['days_b_ahead']}; {a_name} came out ahead in {ci['share_resamples_a_ahead']:.0%} of resamples.")


def ordered(summary: pd.DataFrame, names) -> pd.DataFrame:
    return summary.loc[[n for n in names if n in summary.index]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prices", help="IESO yearly HOEP csv")
    src.add_argument("--synthetic", action="store_true", help="use made-up prices (not a result)")
    ap.add_argument("--kappa", type=float, default=0.0, help="wear cost, $ per MWh charged or discharged")
    ap.add_argument("--tune-until", default=None,
                    help="last day used to tune the threshold rules (default: 30 June of the data's first year)")
    ap.add_argument("--sweep-kappa", default=None, help="comma-separated wear costs to sweep")
    ap.add_argument("--lp-check", action="store_true", help="also solve without the binary variables")
    ap.add_argument("--out", default=None, help="output folder (default results/ or results/synthetic/)")
    ap.add_argument("--no-predispatch", action="store_true", help="skip the strategies on pre-dispatch prices")
    ap.add_argument("--mlflow", action="store_true", help="log the backtest to MLflow")
    ap.add_argument("--mlflow-uri", default=None, help="MLflow tracking URI (default: sqlite:///mlflow.db here)")
    ap.add_argument("--run-name", default=None, help="name for the MLflow run")
    args = ap.parse_args()

    if args.synthetic:
        prices, source = synthetic_prices(), "SYNTHETIC prices (made up). Nothing below is a result."
        predispatch = synthetic_predispatch(prices)
        out = Path(args.out or "results/synthetic")
    else:
        prices, source = load_ieso_hoep(args.prices), f"IESO HOEP, file `{Path(args.prices).name}`"
        predispatch = load_ieso_predispatch(args.prices)
        out = Path(args.out or "results")
    if args.no_predispatch:
        predispatch = None
    if predispatch is not None:
        predispatch = {k: v.reindex(prices.index).to_numpy() for k, v in predispatch.items()}
    (out / "figures").mkdir(parents=True, exist_ok=True)

    battery = Battery(degradation_cost=args.kappa)
    facts = describe_prices(prices)
    tune_until = pd.Timestamp(args.tune_until or f"{prices.index.min().year}-06-30")
    in_tune = prices.index <= tune_until
    tune_days = in_tune.copy()
    tune_days[0] = False                                   # day 0 is only "yesterday" for day 1
    if not tune_days.any() or in_tune.all():
        raise SystemExit("--tune-until must leave days on both sides of it, after the first day")
    P = prices.to_numpy()
    evaluated = prices.iloc[1:]

    def settle_all(name, schedules, bat=battery, **kw):
        """Daily results from day 1 on."""
        return daily_results(name, schedules[1:], evaluated, bat, **kw)

    # 1. Solve. Persistence reuses the perfect-foresight schedules, shifted by one day.
    t0 = time.perf_counter()
    pf = perfect_foresight(P, battery)
    solve_seconds = time.perf_counter() - t0
    ps = persistence(pf, battery)
    rules = {}                                             # name: (low, high, (low_pct, high_pct))
    low, high, _, pcts = tune_threshold(P[tune_days], battery)
    rules[RULE] = (low, high, pcts)
    schedules = {PF: pf, PERSIST: ps, RULE: threshold(P, low, high, battery)}

    if predispatch is not None:
        for name, horizon in [(RULE_1H, 1), (RULE_3H, 3)]:
            seen = fill_with_persistence(predispatch[horizon], P)
            lo, hi, _, pc = tune_threshold(P[tune_days], battery, decision_prices=seen[tune_days])
            rules[name] = (lo, hi, pc)
            schedules[name] = threshold(seen, lo, hi, battery)
        unbiased = {k: remove_bias(v, P) for k, v in predispatch.items()}
        t1 = time.perf_counter()
        schedules[OPT_1H] = rolling_optimizer(P, predispatch, battery, ONE_HOUR_AHEAD)
        rolling_seconds = time.perf_counter() - t1
        schedules[OPT_3H] = rolling_optimizer(P, predispatch, battery, THREE_HOURS_AHEAD)
        schedules[OPT_1H_UNBIASED] = rolling_optimizer(P, unbiased, battery, ONE_HOUR_AHEAD)
        schedules[OPT_3H_UNBIASED] = rolling_optimizer(P, unbiased, battery, THREE_HOURS_AHEAD)
        # A check of the method rather than a strategy: the same 3-hour-ahead
        # re-planning, given each hour's actual price in place of the forecast.
        schedules[OPT_3H_EXACT] = rolling_optimizer(P, {3: P.copy()}, battery, THREE_HOURS_AHEAD)
        missing_pd = {k: int(np.isnan(v).sum()) for k, v in predispatch.items()}
        errors = []                                        # forecast minus actual, real forecasts only
        for k in sorted(predispatch):
            e = predispatch[k] - P
            first, second = e[in_tune], e[~in_tune]
            errors.append({"horizon": k, "share_above": float((e[~np.isnan(e)] > 0).mean()),
                           "mean": float(np.nanmean(e)), "mean_tuning": float(np.nanmean(first)),
                           "mean_test": float(np.nanmean(second))})

    # 2. Settle everything at actual prices, from day 1 on.
    daily = pd.concat([settle_all(name, s) for name, s in schedules.items()], ignore_index=True)
    daily.to_csv(out / "daily_results.csv", index=False)

    full = summarize(daily[daily["strategy"].isin(UNTUNED)], battery)    # the rules were tuned on part of it
    full = ordered(full, UNTUNED)
    test_daily = daily[daily["date"] > tune_until]
    test = ordered(summarize(test_daily[test_daily["strategy"].isin(MAIN)], battery), MAIN)
    test_start = prices.index[~in_tune][0]

    ci = {"value_of_information_test": paired_gap_interval(test_daily, PF, PERSIST),
          "value_of_information_year": paired_gap_interval(daily, PF, PERSIST),
          "rule_actual_prices_minus_persistence_test": paired_gap_interval(test_daily, RULE, PERSIST)}
    if predispatch is not None:
        ci.update({
            "optimizer_minus_rule_1h_test": paired_gap_interval(test_daily, OPT_1H, RULE_1H),
            "optimizer_minus_rule_3h_test": paired_gap_interval(test_daily, OPT_3H, RULE_3H),
            "replanning_1h_minus_persistence_test": paired_gap_interval(test_daily, OPT_1H, PERSIST),
            "replanning_3h_minus_persistence_test": paired_gap_interval(test_daily, OPT_3H, PERSIST),
            "replanning_1h_minus_persistence_year": paired_gap_interval(daily, OPT_1H, PERSIST),
            "replanning_3h_minus_persistence_year": paired_gap_interval(daily, OPT_3H, PERSIST),
            "replanning_1h_bias_removed_minus_persistence_test": paired_gap_interval(test_daily, OPT_1H_UNBIASED, PERSIST),
            "replanning_3h_bias_removed_minus_persistence_test": paired_gap_interval(test_daily, OPT_3H_UNBIASED, PERSIST),
            "replanning_3h_actual_price_minus_persistence_test": paired_gap_interval(test_daily, OPT_3H_EXACT, PERSIST),
            "replanning_3h_actual_price_minus_forecast_test": paired_gap_interval(test_daily, OPT_3H_EXACT, OPT_3H),
        })

    # The test-period day with the highest price, and the comparisons without it.
    test_prices = prices.loc[prices.index > tune_until]
    peak_day = test_prices.max(axis=1).idxmax()
    peak_price = float(test_prices.loc[peak_day].max())
    on_peak = test_daily[test_daily["date"] == peak_day].set_index("strategy")["net_revenue"]
    without = test_daily[test_daily["date"] != peak_day].groupby("strategy")["net_revenue"].sum()

    # 3. Figures.
    plots.cumulative_revenue(test_daily, out / "figures" / "cumulative_revenue.png",
                             f"Cumulative net revenue, test period from {test_start:%d %b %Y}")
    week_start = test_start + pd.Timedelta(days=(7 - test_start.dayofweek) % 7)      # first Monday
    week = {n: schedules[n] for n in MAIN if n in schedules}
    if not plots.sample_week(prices, week, week_start, out / "figures" / "sample_week.png",
                             f"One week of dispatch, from {week_start:%d %b %Y}"):
        print("No days left for the one-week figure; skipped.")
    plots.price_heatmap(prices, out / "figures" / "price_heatmap.png", "Hourly price by day and hour")

    # 4. Write-up.
    b = battery
    rule_lines = [f"- {name}: charge at or below ${lo:.2f} ({pc[0]}th percentile), discharge at or above "
                  f"${hi:.2f} ({pc[1]}th percentile)" for name, (lo, hi, pc) in rules.items()]
    voi_t, voi_y = ci["value_of_information_test"], ci["value_of_information_year"]
    md = [
        "# Results", "",
        f"Source: {source}", "",
        f"- Price file: {facts['first_day']} to {facts['last_day']} ({facts['days']} days, {facts['hours']:,} hours); "
        f"mean {facts['mean_price']:.2f}, min {facts['min_price']:.2f}, max {facts['max_price']:.2f} $/MWh; "
        f"{facts['negative_hours']} negative hours, {facts['zero_hours']} zero hours",
        f"- Evaluated: {evaluated.index[0]:%Y-%m-%d} to {evaluated.index[-1]:%Y-%m-%d} ({len(evaluated)} days). "
        "The first day only serves as \"yesterday\" for the second.",
        f"- Battery: {b.power_mw:g} MW / {b.energy_mwh:g} MWh, round-trip efficiency {b.round_trip_eff:.0%}, "
        f"state of charge {b.soc_min_frac:.0%}-{b.soc_max_frac:.0%}, each day starts and ends at {b.soc_start_frac:.0%}",
        f"- Wear cost: ${b.degradation_cost:g} per MWh charged or discharged",
        "- Price-taker, energy arbitrage only, no fees, charges or other revenue streams",
        f"- {len(pf)} daily MILPs solved in {solve_seconds:.1f} s", "",
        "## Whole period", "",
        "Only the strategies with nothing tuned to the data; the threshold rules were tuned on part of it.", "",
        table(full), "",
        f"- Value of information (perfect foresight minus persistence): **{dollars(voi_y['gap'])}**, "
        f"95% interval {dollars(voi_y['low'])} to {dollars(voi_y['high'])}",
    ]
    if predispatch is not None:
        r1, r3 = ci["replanning_1h_minus_persistence_year"], ci["replanning_3h_minus_persistence_year"]
        md += [f"- Re-planning hourly on the 1-hour-ahead forecast minus persistence: **{dollars(r1['gap'])}**, "
               f"95% interval {dollars(r1['low'])} to {dollars(r1['high'])}",
               f"- Re-planning hourly on the 3-hour-ahead forecast minus persistence: **{dollars(r3['gap'])}**, "
               f"95% interval {dollars(r3['low'])} to {dollars(r3['high'])}"]
    md += [
        "", f"## Test period ({test_start:%d %b %Y} onward)", "",
        f"Threshold rules tuned on {evaluated.index[0]:%d %b %Y} to {tune_until:%d %b %Y}, trying every pair of "
        "percentiles (5th to 95th, in steps of 5) with the lower one below the upper one:", "",
        *rule_lines, "",
        table(test), "",
        "### Comparisons", "",
        "Each gap is taken day by day (both strategies face the same prices), and the daily gaps are resampled in "
        "blocks of 7 consecutive days, 10,000 times (a moving-block bootstrap), for the 95% interval.", "",
        gap_line("Value of information (perfect foresight minus persistence)", voi_t, PF, PERSIST),
        gap_line("Threshold rule on actual prices minus persistence (not achievable: the rule acts on each "
                 "hour's price, known only once the hour is over)", ci["rule_actual_prices_minus_persistence_test"],
                 "the rule", PERSIST),
    ]
    if predispatch is not None:
        exact_vs_fc = ci["replanning_3h_actual_price_minus_forecast_test"]
        exact_vs_ps = ci["replanning_3h_actual_price_minus_persistence_test"]
        md += [
            gap_line("Optimizer minus rule, both on the 1-hour-ahead forecast",
                     ci["optimizer_minus_rule_1h_test"], "the optimizer", "the rule"),
            gap_line("Optimizer minus rule, both on the 3-hour-ahead forecast",
                     ci["optimizer_minus_rule_3h_test"], "the optimizer", "the rule"),
            gap_line("Re-planning hourly on the 1-hour-ahead forecast minus persistence",
                     ci["replanning_1h_minus_persistence_test"], "re-planning", PERSIST),
            gap_line("Re-planning hourly on the 3-hour-ahead forecast minus persistence",
                     ci["replanning_3h_minus_persistence_test"], "re-planning", PERSIST),
            "",
            "Only the 3-hour-ahead timing is one a real battery could have acted on: in 2024, offers and bids for "
            "an hour were locked two hours before it began (IESO's mandatory window), and the 1-hour-ahead price "
            "is published after that.", "",
            "### The pre-dispatch forecasts", "",
            f"The optimizer solved {24 * (len(P) - 1):,} hourly MILPs in {rolling_seconds:.0f} s for each run. "
            "Missing pre-dispatch values were filled with yesterday's price for the same hour: "
            + ", ".join(f"{n} for the {k}-hour-ahead price" for k, n in missing_pd.items()) + ".", "",
            "Forecast minus actual price, over the hours that have a forecast:", "",
            "| Forecast | Above the actual price | Mean error ($/MWh) | Mean error, Jan-Jun | Mean error, Jul-Dec |",
            "|---|---:|---:|---:|---:|",
            *[f"| {e['horizon']}-hour-ahead | {e['share_above']:.0%} | {e['mean']:+.2f} | {e['mean_tuning']:+.2f} | "
              f"{e['mean_test']:+.2f} |" for e in errors], "",
            "Removing the bias (subtracting each forecast's average error over all earlier days) changes the "
            "re-planning optimizer's test-period revenue as follows:", "",
            f"- 1-hour-ahead: {dollars(test_daily.loc[test_daily.strategy == OPT_1H, 'net_revenue'].sum())} as "
            f"published, {dollars(test_daily.loc[test_daily.strategy == OPT_1H_UNBIASED, 'net_revenue'].sum())} with "
            f"the bias removed; minus persistence, {dollars(ci['replanning_1h_bias_removed_minus_persistence_test']['gap'])} "
            f"(95% interval {dollars(ci['replanning_1h_bias_removed_minus_persistence_test']['low'])} to "
            f"{dollars(ci['replanning_1h_bias_removed_minus_persistence_test']['high'])})",
            f"- 3-hour-ahead: {dollars(test_daily.loc[test_daily.strategy == OPT_3H, 'net_revenue'].sum())} as "
            f"published, {dollars(test_daily.loc[test_daily.strategy == OPT_3H_UNBIASED, 'net_revenue'].sum())} with "
            f"the bias removed; minus persistence, {dollars(ci['replanning_3h_bias_removed_minus_persistence_test']['gap'])} "
            f"(95% interval {dollars(ci['replanning_3h_bias_removed_minus_persistence_test']['low'])} to "
            f"{dollars(ci['replanning_3h_bias_removed_minus_persistence_test']['high'])})", "",
            "As a check of the method, the 3-hour-ahead re-planning was also given each hour's actual price in "
            "place of the forecast. It then earned "
            f"{dollars(test_daily.loc[test_daily.strategy == OPT_3H_EXACT, 'net_revenue'].sum())} in the test period: "
            f"{dollars(exact_vs_fc['gap'])} more than with the forecast (95% interval {dollars(exact_vs_fc['low'])} "
            f"to {dollars(exact_vs_fc['high'])}), and {dollars(exact_vs_ps['gap'])} more than persistence (95% "
            f"interval {dollars(exact_vs_ps['low'])} to {dollars(exact_vs_ps['high'])})."
            + (" So the losses above come from the forecast's errors: with an exact price for the coming hour, "
               "re-planning does not fall behind yesterday's plan."
               if exact_vs_fc["low"] > 0 and exact_vs_ps["high"] > 0
               and ci["replanning_3h_minus_persistence_test"]["high"] < 0 else ""), "",
        ]
    md += [
        f"### One day: {peak_day:%d %b %Y}", "",
        f"The highest price in the test period, ${peak_price:,.2f}/MWh, came on {peak_day:%d %b %Y}. What each "
        "strategy earned that day, and in the rest of the test period:", "",
        "| Strategy | That day ($) | Share of its test-period total | Rest of the test period ($) |",
        "|---|---:|---:|---:|",
        *[f"| {n} | {money(on_peak[n])} | "
          f"{on_peak[n] / test.loc[n, 'net_revenue']:.0%} | {money(without[n])} |" if test.loc[n, "net_revenue"] > 0
          else f"| {n} | {money(on_peak[n])} | | {money(without[n])} |"
          for n in test.index], "",
    ]

    sweep_rows, lp_gap = [], None
    if args.sweep_kappa:
        replan = predispatch is not None
        head = "| Wear cost ($/MWh) | Perfect foresight ($) | Cycles | Persistence ($) | Cycles | Persistence losing days |"
        line = "|---:|---:|---:|---:|---:|---:|"
        if replan:
            head += " Re-planning, 1-hour-ahead ($) | Re-planning, 3-hour-ahead ($) |"
            line += "---:|---:|"
        md += ["## Wear cost sweep (whole period)", "",
               "Wear is charged on every MWh charged or discharged, so $10/MWh comes to about $64 for a full cycle "
               "of the usable 3.2 MWh.", "", head, line]
        for k in [float(v) for v in args.sweep_kappa.split(",")]:
            bk = replace(battery, degradation_cost=k)
            pf_k = perfect_foresight(P, bk)
            parts = [settle_all(PF, pf_k, bk), settle_all(PERSIST, persistence(pf_k, bk), bk)]
            if replan:
                parts += [settle_all(OPT_1H, rolling_optimizer(P, predispatch, bk, ONE_HOUR_AHEAD), bk),
                          settle_all(OPT_3H, rolling_optimizer(P, predispatch, bk, THREE_HOURS_AHEAD), bk)]
            s = summarize(pd.concat(parts), bk)
            row = {"wear_cost": k, "pf_net_revenue": s.loc[PF, "net_revenue"], "pf_cycles": s.loc[PF, "cycles"],
                   "persistence_net_revenue": s.loc[PERSIST, "net_revenue"],
                   "persistence_cycles": s.loc[PERSIST, "cycles"],
                   "persistence_losing_days": s.loc[PERSIST, "losing_days"]}
            text = (f"| {k:g} | {money(s.loc[PF, 'net_revenue'])} | {s.loc[PF, 'cycles']:,.0f} | "
                    f"{money(s.loc[PERSIST, 'net_revenue'])} | {s.loc[PERSIST, 'cycles']:,.0f} | "
                    f"{int(s.loc[PERSIST, 'losing_days'])} |")
            if replan:
                row.update({"replanning_1h_net_revenue": s.loc[OPT_1H, "net_revenue"],
                            "replanning_3h_net_revenue": s.loc[OPT_3H, "net_revenue"]})
                text += f" {money(s.loc[OPT_1H, 'net_revenue'])} | {money(s.loc[OPT_3H, 'net_revenue'])} |"
            sweep_rows.append(row)
            md.append(text)
        md.append("")

    if args.lp_check:
        lp_daily = settle_all(PF, perfect_foresight(P, battery, binary=False), allow_simultaneous=True)
        lp = summarize(lp_daily, battery)
        lp_gap = float(lp.loc[PF, "net_revenue"] - full.loc[PF, "net_revenue"])
        md += ["## LP relaxation vs MILP (perfect foresight, whole period)", "",
               f"- MILP: {dollars(full.loc[PF, 'net_revenue'])}",
               f"- LP (charging and discharging in the same hour allowed): {dollars(lp.loc[PF, 'net_revenue'])}",
               f"- Difference: ${lp_gap:,.2f}", ""]

    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    print(f"\nWrote {out/'summary.md'}, {out/'daily_results.csv'} and three figures in {out/'figures'}")

    if args.mlflow:
        from battery_dispatch.tracking import file_fingerprint, log_backtest
        settings = {"tune_until": f"{tune_until:%Y-%m-%d}", "daily_solve_seconds": solve_seconds,
                    "predispatch_comparison": predispatch is not None,
                    "peak_day": f"{peak_day:%Y-%m-%d}", "peak_price": peak_price}
        for name, key in [(RULE, "rule"), (RULE_1H, "rule_1h"), (RULE_3H, "rule_3h")]:
            if name in rules:
                lo, hi, pc = rules[name]
                settings.update({f"{key}_low": lo, f"{key}_high": hi, f"{key}_low_pct": pc[0],
                                 f"{key}_high_pct": pc[1]})
        if predispatch is not None:
            settings["rolling_solve_seconds"] = rolling_seconds
            for e in errors:
                settings[f"predispatch_{e['horizon']}h_share_above_actual"] = e["share_above"]
                settings[f"predispatch_{e['horizon']}h_mean_error"] = e["mean"]
        if lp_gap is not None:
            settings["lp_minus_milp_revenue"] = lp_gap
        data = {"source": "synthetic" if args.synthetic else Path(args.prices).name,
                "fingerprint": "synthetic" if args.synthetic else file_fingerprint(args.prices),
                "first_day": f"{prices.index.min()}", "last_day": f"{prices.index.max()}",
                "days": len(prices), "evaluated_days": len(evaluated), "mean_price": round(float(P.mean()), 2)}
        run_id = log_backtest(battery=battery, data=data, settings=settings,
                              summaries={"whole period": full, "test period": test},
                              comparisons=ci, sweep=sweep_rows,
                              artifacts=[out / "summary.md", out / "daily_results.csv", out / "figures"],
                              tracking_uri=args.mlflow_uri, run_name=args.run_name)
        print(f"Logged to MLflow, run {run_id}. Open the comparison page with: mlflow ui")


if __name__ == "__main__":
    main()
