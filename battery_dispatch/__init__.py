"""Battery storage arbitrage in Ontario: value of information vs value of optimization.

Suggested reading order:
  1. battery.py     the battery's physical parameters
  2. data.py        loading IESO hourly prices (and a synthetic generator for testing)
  3. model.py       the daily optimization model (Pyomo + HiGHS)
  4. simulate.py    settling a schedule at actual prices; the threshold rule
  5. strategies.py  perfect foresight, persistence forecast, threshold rule
  6. evaluate.py    revenue, cycles and the two "value of" numbers
  7. plots.py       figures
"""
