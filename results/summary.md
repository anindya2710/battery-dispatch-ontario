# Results

Source: IESO HOEP, file `PUB_PriceHOEPPredispOR_2024.csv`

- Period: 2024-01-01 to 2024-12-31 (366 days, 8,784 hours)
- Price: mean 32.19, min 0.00, max 1394.13 $/MWh; 0 negative hours, 78 zero hours
- Battery: 1 MW / 4 MWh, round-trip efficiency 88%, state of charge 10%-90%, each day starts and ends at 10%
- Degradation cost: $0 per MWh of throughput
- Price-taker, energy arbitrage only, no fees, charges or other revenue streams
- 366 daily MILPs solved in 5.6 s

## Whole period

The threshold rule is left out here because it was tuned on part of this period.

| Strategy | Net revenue ($) | $/kW-year | Cycles | $/cycle | Losing days | % of perfect foresight |
|---|---:|---:|---:|---:|---:|---:|
| perfect foresight | 46,965 | 46.8 | 585 | 80.3 | 0 | 100% |
| persistence | 15,586 | 15.5 | 583 | 26.7 | 98 | 33% |

Value of information (perfect foresight minus persistence): **$31,379**

## Test period (01 Jul 2024 onward)

Threshold rule tuned on data up to 30 Jun 2024: charge at or below $26.38 (45th percentile), discharge at or above $42.51 (90th percentile).

| Strategy | Net revenue ($) | $/kW-year | Cycles | $/cycle | Losing days | % of perfect foresight |
|---|---:|---:|---:|---:|---:|---:|
| perfect foresight | 26,231 | 52.0 | 275 | 95.4 | 0 | 100% |
| persistence | 9,920 | 19.7 | 276 | 36.0 | 42 | 38% |
| threshold rule | 11,386 | 22.6 | 157 | 72.3 | 12 | 43% |

- Value of information (perfect foresight minus persistence): **$16,311**
- Value of optimization (persistence minus threshold rule): **-$1,466**

## Degradation cost sweep (whole period)

| $/MWh throughput | Perfect foresight net ($) | Cycles | Persistence net ($) | Cycles | Persistence losing days |
|---:|---:|---:|---:|---:|---:|
| 0 | 46,965 | 585 | 15,586 | 583 | 98 |
| 10 | 24,607 | 203 | -3,468 | 203 | 198 |
| 20 | 15,991 | 91 | -6,592 | 91 | 125 |
| 50 | 6,272 | 25 | -6,663 | 25 | 50 |

## LP relaxation vs MILP (perfect foresight, whole period)

- MILP: $46,965
- LP (charging and discharging in the same hour allowed): $46,965
- Difference: $0.06
