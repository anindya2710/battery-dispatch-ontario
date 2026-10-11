"""Lesson 1: the smallest possible storage problem.

You may buy up to 1 MWh at $20, store it at 90% efficiency, and sell what is
left at $50. How much should you buy, and what do you earn?

Run:  python examples/toy_lp.py
Expected output:  optimal 1.0 0.9 25.0     (status, bought, sold, profit)

Exercise (predict first, then change the numbers and run):
  1. Sell price $40.
  2. Sell price $21.
  3. Work out on paper the sell price at which trading stops paying. Confirm it.
"""
import pyomo.environ as pyo

BUY_PRICE = 20.0
SELL_PRICE = 50.0
EFFICIENCY = 0.9

m = pyo.ConcreteModel()
m.buy = pyo.Var(bounds=(0, 1))                  # MWh bought
m.sell = pyo.Var(bounds=(0, None))              # MWh sold
m.cannot_sell_more_than_stored = pyo.Constraint(expr=m.sell <= EFFICIENCY * m.buy)
m.profit = pyo.Objective(expr=SELL_PRICE * m.sell - BUY_PRICE * m.buy, sense=pyo.maximize)

result = pyo.SolverFactory("appsi_highs").solve(m)
print(result.solver.termination_condition, round(pyo.value(m.buy), 4), round(pyo.value(m.sell), 4),
      round(pyo.value(m.profit), 4))
