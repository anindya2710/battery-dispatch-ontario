"""Run the whole analysis and write results/summary.md, a daily CSV and three figures.

If the price file has IESO's pre-dispatch columns (the yearly HOEP report does), it
also runs the fair comparison in battery_dispatch/rolling.py, which takes a few
minutes because the optimizer re-plans every hour.

    python run.py --prices data/raw/PUB_PriceHOEPPredispOR_2024.csv
    python run.py --synthetic                 # made-up prices, to check the code runs

Useful options:
    --kappa 20                  degradation cost in $/MWh of throughput (default 0)
    --sweep-kappa 0,10,20,50    repeat the two optimizing strategies at several costs
    --lp-check                  compare the MILP with its LP relaxation
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
from battery_dispatch.evaluate import (daily_results, paired_gap_interval, summarize, value_of_information,
                                      value_of_optimization)
from battery_dispatch.rolling import fill_with_persistence, rolling_optimizer
from battery_dispatch.strategies import perfect_foresight, persistence, threshold, tune_threshold

PF, PERSIST, RULE = "perfect foresight", "persistence", "threshold rule"
OPT_PD, RULE_PD = "optimizer, pre-dispatch", "threshold rule, pre-dispatch"


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prices", help="IESO yearly HOEP csv")
    src.add_argument("--synthetic", action="store_true", help="use made-up prices (not a result)")
    ap.add_argument("--kappa", type=float, default=0.0, help="degradation cost, $/MWh throughput")
    ap.add_argument("--tune-until", default=None,
                    help="last day used to tune the threshold rule (default: 30 June of the data's first year)")
    ap.add_argument("--sweep-kappa", default=None, help="comma-separated degradation costs to sweep")
    ap.add_argument("--lp-check", action="store_true", help="also solve without the binary variables")
    ap.add_argument("--out", default=None, help="output folder (default results/ or results/synthetic/)")
    ap.add_argument("--no-predispatch", action="store_true", help="skip the fair comparison on pre-dispatch prices")
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
    if in_tune.all() or not in_tune.any():
        raise SystemExit("--tune-until must fall inside the data so that both a tuning and a test period exist")
    P = prices.to_numpy()

    # 1. Solve. Persistence reuses the perfect-foresight schedules, shifted by one day.
    t0 = time.perf_counter()
    pf = perfect_foresight(P, battery)
    solve_seconds = time.perf_counter() - t0
    ps = persistence(pf, battery)
    low, high, tune_net, pcts = tune_threshold(P[in_tune], battery)
    th = threshold(P, low, high, battery)
    if predispatch is not None:
        seen = fill_with_persistence(predispatch[1], P)        # what the rule acts on
        low_pd, high_pd, _, pcts_pd = tune_threshold(P[in_tune], battery, decision_prices=seen[in_tune])
        th_pd = threshold(seen, low_pd, high_pd, battery)
        t1 = time.perf_counter()
        opt_pd = rolling_optimizer(P, predispatch, battery)
        rolling_seconds = time.perf_counter() - t1
        missing_pd = int(sum(np.isnan(v).sum() for v in predispatch.values()))
        over = seen - P                                         # 1-hour-ahead forecast error
        bias_share, bias_mean = float((over > 0).mean()), float(over.mean())

    # 2. Settle everything at actual prices.
    parts = [
        daily_results(PF, pf, prices, battery),
        daily_results(PERSIST, ps, prices, battery),
        daily_results(RULE, th, prices, battery),
    ]
    if predispatch is not None:
        parts += [daily_results(OPT_PD, opt_pd, prices, battery), daily_results(RULE_PD, th_pd, prices, battery)]
    daily = pd.concat(parts, ignore_index=True)
    daily.to_csv(out / "daily_results.csv", index=False)

    full = summarize(daily[~daily["strategy"].isin([RULE, RULE_PD])], battery)   # the rules were tuned on part of it
    test_daily = daily[daily["date"] > tune_until]
    test = summarize(test_daily, battery)
    voi_full, voi_test, voo_test = value_of_information(full), value_of_information(test), value_of_optimization(test)
    voi_ci = paired_gap_interval(test_daily, PF, PERSIST)
    voo_ci = paired_gap_interval(test_daily, PERSIST, RULE)

    # 3. Figures.
    test_start = prices.index[~in_tune][0]
    plots.cumulative_revenue(test_daily, out / "figures" / "cumulative_revenue.png",
                             f"Cumulative net revenue, test period from {test_start:%d %b %Y}")
    week_start = test_start + pd.Timedelta(days=(7 - test_start.dayofweek) % 7)      # first Monday
    plots.sample_week(prices, {PF: pf, PERSIST: ps, RULE: th}, week_start,
                      out / "figures" / "sample_week.png", f"One week of dispatch, from {week_start:%d %b %Y}")
    plots.price_heatmap(prices, out / "figures" / "price_heatmap.png", "Hourly price by day and hour")

    # 4. Write-up.
    b = battery
    md = [
        "# Results", "",
        f"Source: {source}", "",
        f"- Period: {facts['first_day']} to {facts['last_day']} ({facts['days']} days, {facts['hours']:,} hours)",
        f"- Price: mean {facts['mean_price']:.2f}, min {facts['min_price']:.2f}, max {facts['max_price']:.2f} $/MWh; "
        f"{facts['negative_hours']} negative hours, {facts['zero_hours']} zero hours",
        f"- Battery: {b.power_mw:g} MW / {b.energy_mwh:g} MWh, round-trip efficiency {b.round_trip_eff:.0%}, "
        f"state of charge {b.soc_min_frac:.0%}-{b.soc_max_frac:.0%}, each day starts and ends at {b.soc_start_frac:.0%}",
        f"- Degradation cost: ${b.degradation_cost:g} per MWh of throughput",
        "- Price-taker, energy arbitrage only, no fees, charges or other revenue streams",
        f"- {len(pf)} daily MILPs solved in {solve_seconds:.1f} s", "",
        "## Whole period", "",
        "The threshold rule is left out here because it was tuned on part of this period.", "",
        table(full), "",
        f"Value of information (perfect foresight minus persistence): **{dollars(voi_full)}**", "",
        f"## Test period ({test_start:%d %b %Y} onward)", "",
        f"Threshold rule tuned on data up to {tune_until:%d %b %Y}: charge at or below ${low:.2f} "
        f"({pcts[0]}th percentile), discharge at or above ${high:.2f} ({pcts[1]}th percentile).", "",
        table(test), "",
        f"- Value of information (perfect foresight minus persistence): **{dollars(voi_test)}**",
        f"- Persistence minus threshold rule: **{dollars(voo_test)}**. This is not a fair test of optimization: "
        "the rule acts on each hour's actual price, which the persistence plan never sees.", "",
        "### How sure are these two numbers?", "",
        "Each gap is taken day by day (both strategies face the same prices), and the daily gaps are "
        "resampled in blocks of 7 consecutive days, 10,000 times (a moving-block bootstrap).", "",
        f"- Value of information: {dollars(voi_ci['gap'])}, 95% interval {dollars(voi_ci['low'])} to "
        f"{dollars(voi_ci['high'])}",
        f"- Persistence minus threshold rule: {dollars(voo_ci['gap'])}, 95% interval {dollars(voo_ci['low'])} to "
        f"{dollars(voo_ci['high'])}. Persistence earned more on {voo_ci['days_a_ahead']} of {voo_ci['days']} days, "
        f"the rule on {voo_ci['days_b_ahead']}; persistence came out ahead in "
        f"{voo_ci['share_resamples_a_ahead']:.0%} of resamples.", "",
    ]

    if predispatch is not None:
        fair_ci = paired_gap_interval(test_daily, OPT_PD, RULE_PD)
        replan_ci = paired_gap_interval(test_daily, OPT_PD, PERSIST)
        replan_year = paired_gap_interval(daily, OPT_PD, PERSIST)
        md += [
            "## Fair comparison: both strategies on pre-dispatch prices (test period)", "",
            "Both strategies now act only on what was known before each hour started. The rule charges or "
            "discharges on IESO's 1-hour-ahead pre-dispatch price. The optimizer re-plans the rest of the day at "
            "the start of every hour, using the 1-, 2- and 3-hour-ahead pre-dispatch prices for the next three "
            "hours and yesterday's prices after that, and carries out only the first hour of each plan. Both are "
            "settled at actual prices.", "",
            f"Rule tuned on pre-dispatch prices up to {tune_until:%d %b %Y}: charge at or below ${low_pd:.2f} "
            f"({pcts_pd[0]}th percentile), discharge at or above ${high_pd:.2f} ({pcts_pd[1]}th percentile). "
            f"{missing_pd} missing pre-dispatch values were filled with yesterday's price. "
            f"The optimizer solved {24 * (len(P) - 1):,} hourly MILPs in {rolling_seconds:.0f} s. "
            f"Over the year, the 1-hour-ahead pre-dispatch price was above the actual price in {bias_share:.0%} "
            f"of hours, by ${bias_mean:.2f}/MWh on average.", "",
            table(test.loc[[n for n in [PF, OPT_PD, RULE_PD, PERSIST] if n in test.index]]), "",
            f"- Optimizer minus rule, both on pre-dispatch prices (the value of optimization): "
            f"**{dollars(fair_ci['gap'])}**, 95% interval {dollars(fair_ci['low'])} to {dollars(fair_ci['high'])}. "
            f"The optimizer earned more on {fair_ci['days_a_ahead']} of {fair_ci['days']} days, the rule on "
            f"{fair_ci['days_b_ahead']}; the optimizer came out ahead in {fair_ci['share_resamples_a_ahead']:.0%} "
            "of resamples.",
            f"- Re-planning hourly on pre-dispatch prices instead of committing to yesterday's plan: "
            f"**{dollars(replan_ci['gap'])}**, 95% interval {dollars(replan_ci['low'])} to "
            f"{dollars(replan_ci['high'])}. Over the whole year (the optimizer is not tuned, so the first half "
            f"is fair to include): {dollars(replan_year['gap'])}, 95% interval {dollars(replan_year['low'])} to "
            f"{dollars(replan_year['high'])}.", "",
        ]

    if args.sweep_kappa:
        md += ["## Degradation cost sweep (whole period)", "",
               "| $/MWh throughput | Perfect foresight net ($) | Cycles | Persistence net ($) | Cycles | Persistence losing days |",
               "|---:|---:|---:|---:|---:|---:|"]
        for k in [float(v) for v in args.sweep_kappa.split(",")]:
            bk = replace(battery, degradation_cost=k)
            pf_k = perfect_foresight(P, bk)
            s = summarize(pd.concat([daily_results(PF, pf_k, prices, bk),
                                     daily_results(PERSIST, persistence(pf_k, bk), prices, bk)]), bk)
            md.append(f"| {k:g} | {money(s.loc[PF, 'net_revenue'])} | {s.loc[PF, 'cycles']:,.0f} | "
                      f"{money(s.loc[PERSIST, 'net_revenue'])} | {s.loc[PERSIST, 'cycles']:,.0f} | "
                      f"{int(s.loc[PERSIST, 'losing_days'])} |")
        md.append("")

    if args.lp_check:
        lp = summarize(daily_results(PF, perfect_foresight(P, battery, binary=False), prices, battery), battery)
        diff = lp.loc[PF, "net_revenue"] - full.loc[PF, "net_revenue"]
        md += ["## LP relaxation vs MILP (perfect foresight, whole period)", "",
               f"- MILP: {dollars(full.loc[PF, 'net_revenue'])}",
               f"- LP (charging and discharging in the same hour allowed): {dollars(lp.loc[PF, 'net_revenue'])}",
               f"- Difference: ${diff:,.2f}", ""]

    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    print(f"\nWrote {out/'summary.md'}, {out/'daily_results.csv'} and three figures in {out/'figures'}")


if __name__ == "__main__":
    main()
