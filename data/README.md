# Data

The analysis uses the IESO's hourly Ontario energy price (HOEP) for 2024, the last
full calendar year before HOEP was retired on 30 April 2025.

## Download

1. Open https://reports-public.ieso.ca/public/PriceHOEPPredispOR/
2. Download `PUB_PriceHOEPPredispOR_2024.csv` (about 454 KB). Ignore the `_v395`
   copy next to it; it is a versioned duplicate.
3. Save it in this repository as `data/raw/PUB_PriceHOEPPredispOR_2024.csv`.

The IESO Data Directory lists the same report under "Hourly Ontario Energy Price
(HOEP) Reports": https://ieso.ca/Power-Data/Data-Directory

## What is in the file

- Three comment lines starting with `\\`, then a header row.
- Columns: `Date, Hour, HOEP, Hour 1 Predispatch, Hour 2 Predispatch, Hour 3 Predispatch,
  OR 10 Min Sync, OR 10 Min non-sync, OR 30 Min`. Only `Date`, `Hour` and `HOEP` are used.
- `Hour` runs 1-24 and is hour ending (Hour 1 is 00:00-01:00), in Eastern Standard
  Time all year, so every day has 24 hours. 2024 is a leap year: 366 days, 8,784 hours.
- Prices are in $/MWh and can be negative. Some predispatch fields are blank.

## Check after loading

`run.py` prints the number of days and hours, the mean price and the count of
negative hours. For 2024 expect 366 days and 8,784 hours. The IESO's published
monthly averages for 2024 run from about 2.8 to 4.3 cents/kWh (demand-weighted),
so a mean price far outside roughly $28-43/MWh means the file was read wrongly.
