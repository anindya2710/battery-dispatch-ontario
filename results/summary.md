# Results

Source: IESO HOEP, file `PUB_PriceHOEPPredispOR_2024.csv`

- Period: 2024-01-01 to 2024-12-31 (366 days, 8,784 hours)
- Price: mean 32.19, min 0.00, max 1394.13 $/MWh; 0 negative hours, 78 zero hours
- Battery: 1 MW / 4 MWh, round-trip efficiency 88%, state of charge 10%-90%, each day starts and ends at 10%
- Degradation cost: $0 per MWh of throughput
- Price-taker, energy arbitrage only, no fees, charges or other revenue streams
- 366 daily MILPs solved in 4.5 s

## Whole period

The threshold rule is left out here because it was tuned on part of this period.

| Strategy | Net revenue ($) | $/kW-year | Cycles | $/cycle | Losing days | % of perfect foresight |
|---|---:|---:|---:|---:|---:|---:|
| perfect foresight | 46,965 | 46.8 | 585 | 80.3 | 0 | 100% |
| persistence | 15,564 | 15.5 | 583 | 26.7 | 99 | 33% |
| optimizer, pre-dispatch | 20,144 | 20.1 | 528 | 38.2 | 46 | 43% |

Value of information (perfect foresight minus persistence): **$31,401**

## Test period (01 Jul 2024 onward)

Threshold rule tuned on data up to 30 Jun 2024: charge at or below $26.38 (45th percentile), discharge at or above $42.51 (90th percentile).

| Strategy | Net revenue ($) | $/kW-year | Cycles | $/cycle | Losing days | % of perfect foresight |
|---|---:|---:|---:|---:|---:|---:|
| perfect foresight | 26,231 | 52.0 | 275 | 95.5 | 0 | 100% |
| persistence | 9,923 | 19.7 | 276 | 36.0 | 42 | 38% |
| threshold rule | 11,386 | 22.6 | 157 | 72.3 | 12 | 43% |
| optimizer, pre-dispatch | 10,998 | 21.8 | 250 | 43.9 | 24 | 42% |
| threshold rule, pre-dispatch | 3,867 | 7.7 | 216 | 17.9 | 51 | 15% |

- Value of information (perfect foresight minus persistence): **$16,308**
- Persistence minus threshold rule: **-$1,463**. This is not a fair test of optimization: the rule acts on each hour's actual price, which the persistence plan never sees.

### How sure are these two numbers?

Each gap is taken day by day (both strategies face the same prices), and the daily gaps are resampled in blocks of 7 consecutive days, 10,000 times (a moving-block bootstrap).

- Value of information: $16,308, 95% interval $13,143 to $19,652
- Persistence minus threshold rule: -$1,463, 95% interval -$5,290 to $3,603. Persistence earned more on 65 of 184 days, the rule on 119; persistence came out ahead in 27% of resamples.

## Fair comparison: both strategies on pre-dispatch prices (test period)

Both strategies now act only on what was known before each hour started. The rule charges or discharges on IESO's 1-hour-ahead pre-dispatch price. The optimizer re-plans the rest of the day at the start of every hour, using the 1-, 2- and 3-hour-ahead pre-dispatch prices for the next three hours and yesterday's prices after that, and carries out only the first hour of each plan. Both are settled at actual prices.

Rule tuned on pre-dispatch prices up to 30 Jun 2024: charge at or below $30.26 (45th percentile), discharge at or above $34.25 (60th percentile). 51 missing pre-dispatch values were filled with yesterday's price. The optimizer solved 8,760 hourly MILPs in 83 s. Over the year, the 1-hour-ahead pre-dispatch price was above the actual price in 79% of hours, by $6.83/MWh on average.

| Strategy | Net revenue ($) | $/kW-year | Cycles | $/cycle | Losing days | % of perfect foresight |
|---|---:|---:|---:|---:|---:|---:|
| perfect foresight | 26,231 | 52.0 | 275 | 95.5 | 0 | 100% |
| optimizer, pre-dispatch | 10,998 | 21.8 | 250 | 43.9 | 24 | 42% |
| threshold rule, pre-dispatch | 3,867 | 7.7 | 216 | 17.9 | 51 | 15% |
| persistence | 9,923 | 19.7 | 276 | 36.0 | 42 | 38% |

- Optimizer minus rule, both on pre-dispatch prices (the value of optimization): **$7,130**, 95% interval $4,865 to $10,163. The optimizer earned more on 145 of 184 days, the rule on 39; the optimizer came out ahead in 100% of resamples.
- Re-planning hourly on pre-dispatch prices instead of committing to yesterday's plan: **$1,075**, 95% interval -$2,692 to $3,745. Over the whole year (the optimizer is not tuned, so the first half is fair to include): $4,581, 95% interval $60 to $8,725.

## Degradation cost sweep (whole period)

| $/MWh throughput | Perfect foresight net ($) | Cycles | Persistence net ($) | Cycles | Persistence losing days |
|---:|---:|---:|---:|---:|---:|
| 0 | 46,965 | 585 | 15,564 | 583 | 99 |
| 10 | 24,607 | 203 | -3,451 | 203 | 198 |
| 20 | 15,991 | 91 | -6,602 | 91 | 125 |
| 50 | 6,272 | 25 | -6,671 | 25 | 50 |

## LP relaxation vs MILP (perfect foresight, whole period)

- MILP: $46,965
- LP (charging and discharging in the same hour allowed): $46,965
- Difference: $0.06
