"""Run the whole analysis and write results/summary.md, a daily CSV and three figures.

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

import pandas as pd

from battery_dispatch import plots
from battery_dispatch.battery import Battery
from battery_dispatch.data import describe_prices, load_ieso_hoep, synthetic_prices
from battery_dispatch.evaluate import daily_results, summarize, value_of_information, value_of_optimization
from battery_dispatch.strategies import perfect_foresight, persistence, threshold, tune_threshold

PF, PERSIST, RULE = "perfect foresight", "persistence", "threshold rule"


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
    args = ap.parse_args()

    if args.synthetic:
        prices, source = synthetic_prices(), "SYNTHETIC prices (made up). Nothing below is a result."
        out = Path(args.out or "results/synthetic")
    else:
        prices, source = load_ieso_hoep(args.prices), f"IESO HOEP, file `{Path(args.prices).name}`"
        out = Path(args.out or "results")
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

    # 2. Settle everything at actual prices.
    daily = pd.concat([
        daily_results(PF, pf, prices, battery),
        daily_results(PERSIST, ps, prices, battery),
        daily_results(RULE, th, prices, battery),
    ], ignore_index=True)
    daily.to_csv(out / "daily_results.csv", index=False)

    full = summarize(daily[daily["strategy"] != RULE], battery)      # the rule saw part of this period
    test_daily = daily[daily["date"] > tune_until]
    test = summarize(test_daily, battery)
    voi_full, voi_test, voo_test = value_of_information(full), value_of_information(test), value_of_optimization(test)

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
        f"- Value of optimization (persistence minus threshold rule): **{dollars(voo_test)}**", "",
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
