# Results

Source: IESO HOEP, file `PUB_PriceHOEPPredispOR_2024.csv`

- Price file: 2024-01-01 to 2024-12-31 (366 days, 8,784 hours); mean 32.19, min 0.00, max 1394.13 $/MWh; 0 negative hours, 78 zero hours
- Evaluated: 2024-01-02 to 2024-12-31 (365 days). The first day only serves as "yesterday" for the second.
- Battery: 1 MW / 4 MWh, round-trip efficiency 88%, state of charge 10%-90%, each day starts and ends at 10%
- Wear cost: $0 per MWh charged or discharged
- Price-taker, energy arbitrage only, no fees, charges or other revenue streams
- 366 daily MILPs solved in 4.6 s

## Whole period

Only the strategies with nothing tuned to the data; the threshold rules were tuned on part of it.

| Strategy | Net revenue ($) | $/kW-year | Cycles | $/cycle | Losing days | % of perfect foresight |
|---|---:|---:|---:|---:|---:|---:|
| perfect foresight | 46,941 | 46.9 | 584 | 80.4 | 0 | 100% |
| persistence | 15,561 | 15.6 | 583 | 26.7 | 99 | 33% |
| re-planning optimizer, 1-hour-ahead | 20,147 | 20.1 | 528 | 38.2 | 45 | 43% |
| re-planning optimizer, 3-hour-ahead | 8,391 | 8.4 | 420 | 20.0 | 111 | 18% |

- Value of information (perfect foresight minus persistence): **$31,380**, 95% interval $26,843 to $36,515
- Re-planning hourly on the 1-hour-ahead forecast minus persistence: **$4,586**, 95% interval -$74 to $8,800
- Re-planning hourly on the 3-hour-ahead forecast minus persistence: **-$7,170**, 95% interval -$12,470 to -$3,068

## Test period (01 Jul 2024 onward)

Threshold rules tuned on 02 Jan 2024 to 30 Jun 2024, trying every pair of percentiles (5th to 95th, in steps of 5) with the lower one below the upper one:

- threshold rule, actual prices: charge at or below $28.66 (55th percentile), discharge at or above $42.53 (90th percentile)
- threshold rule, 1-hour-ahead: charge at or below $34.27 (60th percentile), discharge at or above $43.00 (80th percentile)
- threshold rule, 3-hour-ahead: charge at or below $27.63 (40th percentile), discharge at or above $32.68 (60th percentile)

| Strategy | Net revenue ($) | $/kW-year | Cycles | $/cycle | Losing days | % of perfect foresight |
|---|---:|---:|---:|---:|---:|---:|
| perfect foresight | 26,231 | 52.0 | 275 | 95.5 | 0 | 100% |
| persistence | 9,921 | 19.7 | 276 | 36.0 | 42 | 38% |
| threshold rule, actual prices | 13,109 | 26.0 | 188 | 69.8 | 16 | 50% |
| re-planning optimizer, 1-hour-ahead | 10,991 | 21.8 | 251 | 43.9 | 24 | 42% |
| threshold rule, 1-hour-ahead | 7,210 | 14.3 | 231 | 31.2 | 45 | 27% |
| re-planning optimizer, 3-hour-ahead | 3,704 | 7.3 | 204 | 18.1 | 58 | 14% |
| threshold rule, 3-hour-ahead | 4,211 | 8.4 | 168 | 25.0 | 36 | 16% |

### Comparisons

Each gap is taken day by day (both strategies face the same prices), and the daily gaps are resampled in blocks of 7 consecutive days, 10,000 times (a moving-block bootstrap), for the 95% interval.

- Value of information (perfect foresight minus persistence): **$16,310**, 95% interval $13,145 to $19,654. Perfect foresight earned more on 184 of 184 days, persistence on 0; perfect foresight came out ahead in 100% of resamples.
- Threshold rule on actual prices minus persistence (not achievable: the rule acts on each hour's price, known only once the hour is over): **$3,188**, 95% interval -$1,963 to $7,225. The rule earned more on 123 of 184 days, persistence on 61; the rule came out ahead in 90% of resamples.
- Optimizer minus rule, both on the 1-hour-ahead forecast: **$3,781**, 95% interval $676 to $7,116. The optimizer earned more on 132 of 184 days, the rule on 52; the optimizer came out ahead in 99% of resamples.
- Optimizer minus rule, both on the 3-hour-ahead forecast: **-$507**, 95% interval -$2,022 to $1,012. The optimizer earned more on 89 of 184 days, the rule on 88; the optimizer came out ahead in 31% of resamples.
- Re-planning hourly on the 1-hour-ahead forecast minus persistence: **$1,070**, 95% interval -$2,701 to $3,738. Re-planning earned more on 111 of 184 days, persistence on 73; re-planning came out ahead in 74% of resamples.
- Re-planning hourly on the 3-hour-ahead forecast minus persistence: **-$6,217**, 95% interval -$10,814 to -$3,193. Re-planning earned more on 72 of 184 days, persistence on 112; re-planning came out ahead in 0% of resamples.

Only the 3-hour-ahead timing is one a real battery could have acted on: in 2024, offers and bids for an hour were locked two hours before it began (IESO's mandatory window), and the 1-hour-ahead price is published after that.

### The pre-dispatch forecasts

The optimizer solved 8,760 hourly MILPs in 81 s for each run. Missing pre-dispatch values were filled with yesterday's price for the same hour: 17 for the 1-hour-ahead price, 17 for the 2-hour-ahead price, 17 for the 3-hour-ahead price.

Forecast minus actual price, over the hours that have a forecast:

| Forecast | Above the actual price | Mean error ($/MWh) | Mean error, Jan-Jun | Mean error, Jul-Dec |
|---|---:|---:|---:|---:|
| 1-hour-ahead | 79% | +6.85 | +5.90 | +7.82 |
| 2-hour-ahead | 77% | +6.40 | +5.32 | +7.48 |
| 3-hour-ahead | 71% | +4.46 | +3.69 | +5.24 |

Removing the bias (subtracting each forecast's average error over all earlier days) changes the re-planning optimizer's test-period revenue as follows:

- 1-hour-ahead: $10,991 as published, $11,473 with the bias removed; minus persistence, $1,552 (95% interval -$2,301 to $4,394)
- 3-hour-ahead: $3,704 as published, $6,025 with the bias removed; minus persistence, -$3,896 (95% interval -$6,316 to -$2,116)

As a check of the method, the 3-hour-ahead re-planning was also given each hour's actual price in place of the forecast. It then earned $12,507 in the test period: $8,803 more than with the forecast (95% interval $6,333 to $11,806), and $2,586 more than persistence (95% interval -$2,313 to $6,087). So the losses above come from the forecast's errors: with an exact price for the coming hour, re-planning does not fall behind yesterday's plan.

### One day: 25 Aug 2024

The highest price in the test period, $1,394.13/MWh, came on 25 Aug 2024. What each strategy earned that day, and in the rest of the test period:

| Strategy | That day ($) | Share of its test-period total | Rest of the test period ($) |
|---|---:|---:|---:|
| perfect foresight | 2,109 | 8% | 24,122 |
| persistence | 1,891 | 19% | 8,030 |
| threshold rule, actual prices | 94 | 1% | 13,015 |
| re-planning optimizer, 1-hour-ahead | 522 | 5% | 10,468 |
| threshold rule, 1-hour-ahead | 1,420 | 20% | 5,791 |
| re-planning optimizer, 3-hour-ahead | 197 | 5% | 3,507 |
| threshold rule, 3-hour-ahead | 78 | 2% | 4,133 |

## Wear cost sweep (whole period)

Wear is charged on every MWh charged or discharged, so $10/MWh comes to about $64 for a full cycle of the usable 3.2 MWh.

| Wear cost ($/MWh) | Perfect foresight ($) | Cycles | Persistence ($) | Cycles | Persistence losing days | Re-planning, 1-hour-ahead ($) | Re-planning, 3-hour-ahead ($) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 46,941 | 584 | 15,561 | 583 | 99 | 20,147 | 8,391 |
| 10 | 24,607 | 203 | -3,451 | 203 | 198 | 143 | -3,637 |
| 20 | 15,991 | 91 | -6,602 | 91 | 125 | -4,191 | -6,507 |
| 50 | 6,272 | 25 | -6,672 | 25 | 50 | -4,676 | -5,730 |

## LP relaxation vs MILP (perfect foresight, whole period)

- MILP: $46,941
- LP (charging and discharging in the same hour allowed): $46,941
- Difference: $0.00
