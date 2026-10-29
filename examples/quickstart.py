"""Quick start: every module in about thirty lines.

Run from the project folder:  python examples/quickstart.py
"""

from openportfolio.modules import compliance, hub, performance, risk_dashboard, scenarios, trading
from openportfolio.modules.explainer import Explainer
from openportfolio.workspace import Workspace

ws = Workspace.sample()  # swap in your own: Workspace(master, market_data, portfolio)

print(hub.describe(ws))
print(risk_dashboard.stress_summary_sentence(ws))

for m in risk_dashboard.headline(ws)[:4]:
    print(f"  {m['label']}: {m['value']:.2%}  ({m['meaning']})")

print(scenarios.custom(ws, equity=-0.15, rates=0.005)["plain"])
print(performance.summary(ws)["plain"])
print(compliance.default_rulebook().summary(ws)["plain"])

plan = trading.rebalance(ws, trading.optimise(ws, "risk_parity", max_weight=0.2, min_weight=0.01)["weights"])
print(plan["plain"])

print(Explainer(ws).ask("What if the dollar strengthens 10%?")["answer"])
