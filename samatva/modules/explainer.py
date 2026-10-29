"""The AI Explainer — works across every module.

The simple question it answers: *can you explain this to me?*

Two engines sit behind one interface:

* **Built-in engine (default).** Understands common questions in plain words,
  runs the right calculation and answers in a sentence or two. It needs no
  internet connection and no API key, and every answer lists the numbers it
  used, so it can always be checked.
* **Language-model engine (optional).** If an LLM backend is supplied, questions
  the built-in engine cannot place are passed to the model *together with the
  portfolio facts*, and the model is instructed to answer only from those
  facts. The built-in engine still handles everything it recognises.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Dict, List, Optional, Protocol

import numpy as np

from ..analytics import risk as R
from ..glossary import GLOSSARY, format_value
from ..workspace import Workspace
from . import compliance as C
from . import hub
from . import performance as P
from . import risk_dashboard as RD
from . import scenarios as S


class LLMBackend(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


class AnthropicBackend:
    """Optional backend using the Anthropic API (``pip install anthropic`` and set ANTHROPIC_API_KEY)."""

    def __init__(self, model: Optional[str] = None, max_tokens: int = 600) -> None:
        import anthropic  # imported lazily so the package is optional

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("SAMATVA_LLM_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens

    def complete(self, system: str, prompt: str) -> str:
        msg = self.client.messages.create(model=self.model, max_tokens=self.max_tokens, system=system,
                                          messages=[{"role": "user", "content": prompt}])
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


_NUM = r"(\d+(?:\.\d+)?)\s*(%|percent|bps|basis points|pp|percentage points?)?"


def _pct(match_num: str, unit: Optional[str]) -> float:
    v = float(match_num)
    if unit and unit.startswith(("bps", "basis")):
        return v / 10_000
    return v / 100


class Explainer:
    def __init__(self, ws: Workspace, rulebook: Optional[C.RuleBook] = None, funds=None,
                 llm: Optional[LLMBackend] = None, achchu: Optional[Callable] = None) -> None:
        self.ws = ws
        self._achchu_factory = achchu
        self.rulebook = rulebook or C.default_rulebook()
        self.funds = funds or []
        self.llm = llm
        self._intents: List[tuple[str, Callable[[str], Dict]]] = [
            (r"\bwhat if\b|\bscenario\b|\bstress\b|\bcrash\b|\b2008\b|\bpandemic\b|\bcovid\b|"
             r"\b(fall|falls|drop|drops|rise|rises|jump|jumps|strengthen\w*|weaken\w*)\b.*\d", self._what_if),
            (r"\bmean(s|ing)?\b|\bdefin|\bwhat is (a |an |the )?(var|value at risk|sharpe|drawdown|beta|"
             r"tracking error|expected shortfall|duration|dv01|tvpi|dpi|irr|volatility)", self._define),
            (r"\bwhy\b.*\b(down|up|fell|rose|drop|gain|move|moved|change)\b|\btoday\b|\byesterday\b", self._why_moved),
            (r"\bcomplian|\brule|\bbreach|\blimit", self._compliance),
            (r"\bbreak(ing)?\b(?!.*\b(rule|limit|polic|mandate))|\bgive up\b|\bgiving up\b|\bpanic|\bstrain|\bfatigue|\baxle|\bachchu|"
             r"\bhow (much|close).*\b(take|bear|stand|handle)|\bcan i (take|bear|stand|handle)", self._axle),
            (r"\bprivate\b|\bcommitment|\bcapital call|\bunfunded", self._private),
            (r"\bliquid|\bsell (it|everything|quickly)|\bhow (fast|quickly)", self._liquidity),
            (r"\binterest rate|\brates?\b|\bduration\b|\bbond|\bdv01", self._rates),
            (r"\bwhere\b.*\brisk|\brisk\b.*\b(from|coming|driver|biggest|most)|\bcontribut", self._risk_sources),
            (r"\bconcentrat|\bdiversif|\blargest|\bbiggest holding|\btop holding", self._concentration),
            (r"\blose\b|\bloss\b|\bvar\b|\bvalue at risk|\bbad day|\bdownside|\bworst", self._downside),
            (r"\bperform|\breturn|\bbenchmark|\bbeat|\boutperform|\bunderperform", self._performance),
            (r"\bvolatil|\bswing|\bbumpy|\brisky\b|\bhow risky", self._volatility),
            (r"\bworth\b|\bvalue\b|\bnav\b|\bwhat do i own|\bholdings?\b|\bsummar", self._overview),
        ]

    def achchu(self):
        if self._achchu_factory is not None:
            return self._achchu_factory()
        from .achchu import Achchu

        if getattr(self, "_achchu", None) is None:
            self._achchu = Achchu(self.ws)
        return self._achchu

    def _axle(self, q: str) -> Dict:
        r = self.achchu().report()
        f = r["fatigue"]
        return {"answer": r["plain"] + " " + r["capacity"]["plain"], "intent": "axle",
                "sources": [{"label": "Life left", "value": f["life_left"]},
                            {"label": "Capacity", "value": r["capacity"]["capacity"]},
                            {"label": "Chance of breaking within a year", "value": r["break_probability"]}]}

    # ------------------------------------------------------------------ public
    def ask(self, question: str) -> Dict:
        q = question.lower().strip()
        for pattern, handler in self._intents:
            if re.search(pattern, q):
                try:
                    out = handler(q)
                except Exception as exc:  # never let a question crash the app
                    out = {"answer": f"I could not work that out: {exc}", "sources": []}
                out.setdefault("intent", handler.__name__.strip("_"))
                out["engine"] = "built-in"
                return out
        if self.llm is not None:
            return self._ask_llm(question)
        return {
            "intent": "unknown", "engine": "built-in", "sources": [],
            "answer": ("I am not sure how to answer that yet. Try asking: 'How much could I lose?', "
                       "'Where is my risk coming from?', 'What if equities fall 20%?', 'How am I doing against "
                       "the benchmark?', 'How liquid is the portfolio?', 'Am I breaking any rules?' or "
                       "'How close am I to giving up?'"),
        }

    def facts(self) -> Dict:
        """A compact, factual snapshot used to ground any language model."""
        ws = self.ws
        s = hub.summary(ws)
        return {
            "portfolio": s["portfolio"], "as_of": s["as_of"], "nav": round(ws.nav, 2),
            "allocation_by_asset_class": hub.allocation(ws).round(4).to_dict(),
            "top_holdings": ws.valuation.head(5)[["name", "weight"]].round(4).to_dict(orient="records"),
            "risk": {m["label"]: round(m["value"], 5) for m in RD.headline(ws)},
            "top_risk_contributors": RD.risk_contributions(ws).head(3)[["name", "percent"]].round(4)
            .to_dict(orient="records"),
            "performance": P.summary(ws).get("plain"),
            "compliance": self.rulebook.summary(ws)["plain"],
        }

    def commentary(self) -> str:
        """A short daily commentary, written the way a portfolio manager would brief a colleague."""
        ws = self.ws
        hist = ws.nav_history
        chg = hist.iloc[-1] / hist.iloc[-2] - 1
        contrib = self._day_contributions()
        best, worst = contrib.idxmax(), contrib.idxmin()
        vol_now = R.ewma_volatility(ws.portfolio_returns)
        vol_long = R.volatility(ws.portfolio_returns)
        regime = ("higher than" if vol_now > 1.1 * vol_long else "lower than" if vol_now < 0.9 * vol_long
                  else "in line with")
        comp = self.rulebook.summary(ws)
        if contrib[worst] < 0 < contrib[best]:
            movers = (f"{ws.master.get(best).name} helped most ({format_value(contrib[best], 'money')}), while "
                      f"{ws.master.get(worst).name} held it back ({format_value(contrib[worst], 'money')}).")
        elif contrib[worst] >= 0:
            movers = (f"Every holding gained or held steady; {ws.master.get(best).name} added the most "
                      f"({format_value(contrib[best], 'money')}).")
        else:
            movers = (f"Every holding fell or held steady; {ws.master.get(worst).name} fell the most "
                      f"({format_value(contrib[worst], 'money')}).")
        text = (f"The portfolio {'rose' if chg >= 0 else 'fell'} {abs(chg):.2%} on {ws.market.as_of.date()}, "
                f"to {format_value(ws.nav, 'money')}. {movers} Recent volatility of {vol_now:.1%} is {regime} its "
                f"long-run level of {vol_long:.1%}. ")
        text += RD.stress_summary_sentence(ws) + " " + comp["plain"]
        return text

    def anomalies(self, z_threshold: float = 3.0) -> List[Dict]:
        """Things worth a second look today."""
        ws = self.ws
        flags: List[Dict] = []
        r = ws.asset_returns
        last = r.iloc[-1]
        sd = r.iloc[:-1].tail(250).std()
        for iid, v in last.items():
            if sd[iid] > 0 and abs(v / sd[iid]) >= z_threshold:
                flags.append({"type": "unusual_move", "severity": "warning", "subject": ws.master.get(iid).name,
                              "message": f"{ws.master.get(iid).name} moved {v:+.1%} today, about "
                                         f"{abs(v / sd[iid]):.1f} times its usual daily move."})
        vol_now = R.ewma_volatility(ws.portfolio_returns)
        vol_long = R.volatility(ws.portfolio_returns)
        if vol_now > 1.25 * vol_long:
            flags.append({"type": "volatility_regime", "severity": "warning", "subject": "Portfolio",
                          "message": f"Recent volatility ({vol_now:.1%}) is well above normal ({vol_long:.1%})."})
        top = ws.valuation.iloc[0]
        if top["weight"] > 0.15:
            flags.append({"type": "concentration", "severity": "info", "subject": top["name"],
                          "message": f"{top['name']} is {top['weight']:.1%} of the portfolio."})
        for issue in self.rulebook.summary(ws)["issues"]:
            if issue["status"] == "breach":
                flags.append({"type": "compliance", "severity": "breach", "subject": issue["subject"],
                              "message": issue["message"]})
        return flags

    # ------------------------------------------------------------------ handlers
    def _overview(self, q: str) -> Dict:
        ws = self.ws
        return {"answer": hub.describe(self.ws),
                "sources": [{"label": "Net asset value", "value": ws.nav},
                            {"label": "Holdings", "value": len(ws.held_ids)}]}

    def _downside(self, q: str) -> Dict:
        ws = self.ws
        r = ws.portfolio_returns
        var, es = R.historical_var(r, 0.95), R.historical_es(r, 0.95)
        dd = R.drawdown(ws.nav_history)
        answer = (RD.stress_summary_sentence(ws) + f" The largest fall from a peak in the history of today's "
                  f"holdings was {dd.max_drawdown:.1%}.")
        return {"answer": answer, "sources": [{"label": "VaR 95% (1 day)", "value": var},
                                              {"label": "Expected Shortfall 95%", "value": es},
                                              {"label": "Maximum drawdown", "value": dd.max_drawdown}]}

    def _risk_sources(self, q: str) -> Dict:
        rc = RD.risk_contributions(self.ws).head(3)
        parts = [f"{row['name']} ({row['percent']:.0%} of the risk, {row['weight']:.0%} of the money)"
                 for _, row in rc.iterrows()]
        return {"answer": "Most of the risk comes from " + ", ".join(parts[:-1]) + f" and {parts[-1]}. "
                          "A holding can carry far more risk than its size suggests when it is volatile or moves "
                          "with the rest of the portfolio.",
                "sources": [{"label": f"Risk share: {r['name']}", "value": r["percent"]} for _, r in rc.iterrows()]}

    def _what_if(self, q: str) -> Dict:
        ws = self.ws
        named = {"2008": 0, "financial crisis": 0, "pandemic": 1, "covid": 1, "2020": 1, "2022": 2,
                 "inflation": 2, "tech": 3, "emerging": 6}
        for key, idx in named.items():
            if key in q and not re.search(r"\d+\s*%", q.replace("2008", "").replace("2020", "").replace("2022", "")):
                res = S.run(ws, S.LIBRARY[idx])
                return {"answer": res["plain"], "intent": "what_if",
                        "sources": [{"label": "Scenario P&L", "value": res["pnl"]}]}
        shocks: Dict = {}
        down = r"(fall|falls|drop|drops|crash|crashes|decline|declines|lose|loses|down|weaken\w*)"
        up = r"(rise|rises|jump|jumps|gain|gains|up|rally|rallies|climb|climbs|strengthen\w*)"
        targets = {
            "equity": r"(equit\w*|stocks?|shares|market)",
            "rates": r"(rates?|yields?|interest rates?)",
            "real_estate": r"(property|real estate|reits?)",
            "commodity": r"(gold|commodit\w*)",
            "dollar": r"(dollar|usd)",
        }
        for key, noun in targets.items():
            for direction, sign in ((down, -1), (up, 1)):
                m = re.search(noun + r"\D{0,25}?" + direction + r"\D{0,15}?" + _NUM, q)
                if m:
                    val = sign * _pct(m.group(m.lastindex - 1), m.group(m.lastindex))
                    shocks[key] = val
                    break
        if not shocks:
            return {"answer": "Tell me the move you have in mind, for example 'what if equities fall 20%' or "
                              "'what if rates rise 1%'. You can also name a crisis such as 2008 or 2022.",
                    "sources": []}
        kwargs: Dict = {}
        for k in ("equity", "real_estate", "commodity"):
            if k in shocks:
                kwargs[k] = shocks[k]
        if "rates" in shocks:
            kwargs["rates"] = shocks["rates"]
        if "dollar" in shocks:
            d = shocks["dollar"]
            kwargs["fx"] = {c: (1 / (1 + d) - 1) for c in ("EUR", "JPY", "GBP")}
        labels = {"equity": "equities", "rates": "rates", "real_estate": "property", "commodity": "commodities",
                  "dollar": "the dollar"}
        name = ", ".join(f"{labels[k]} {v * 100:+.2f} pp" if k == "rates" else f"{labels[k]} {v:+.0%}"
                         for k, v in shocks.items())
        res = S.custom(ws, name[0].upper() + name[1:], **kwargs)
        return {"answer": res["plain"], "intent": "what_if", "shocks": shocks,
                "sources": [{"label": "Scenario P&L", "value": res["pnl"]},
                            {"label": "Scenario P&L (% of NAV)", "value": res["pnl_pct"]}]}

    def _performance(self, q: str) -> Dict:
        s = P.summary(self.ws)
        pr = s["period_returns"]
        extra = f" This year so far: {pr['YTD']:+.1%}; over the last 12 months: {pr['1Y']:+.1%}." if pr.get("1Y") is not None else ""
        return {"answer": s.get("plain", "") + extra,
                "sources": [{"label": m["label"], "value": m["value"]} for m in s["metrics"]]}

    def _why_moved(self, q: str) -> Dict:
        ws = self.ws
        hist = ws.nav_history
        chg = float(hist.iloc[-1] - hist.iloc[-2])
        contrib = self._day_contributions().sort_values()
        movers = contrib.reindex(contrib.abs().sort_values(ascending=False).index).head(3)
        parts = [f"{ws.master.get(i).name} ({format_value(v, 'money')})" for i, v in movers.items()]
        f = ws.market.factors.iloc[-1] if ws.market.factors is not None else None
        driver = ""
        if f is not None:
            driver = (f" Broad markets moved {f['Market']:+.2%} and bond yields changed by "
                      f"{f['Rates'] * 10_000:+.1f} basis points.")
        return {"answer": (f"The portfolio {'gained' if chg >= 0 else 'lost'} {format_value(abs(chg), 'money')} "
                           f"on the latest day. The biggest movers were {', '.join(parts)}.{driver}"),
                "sources": [{"label": ws.master.get(i).name, "value": float(v)} for i, v in movers.items()]}

    def _rates(self, q: str) -> Dict:
        b = RD.bond_analytics(self.ws)
        return {"answer": b.get("plain", "The portfolio holds no bonds."),
                "sources": [{"label": "Duration", "value": b["portfolio_duration"]["value"]},
                            {"label": "DV01", "value": b["dv01"]["value"]}]}

    def _liquidity(self, q: str) -> Dict:
        l = RD.liquidity(self.ws)
        d = l["days_to_liquidate_90"]["value"]
        slow = l["positions"][0]
        prof = l["profile"]
        return {"answer": (f"About {prof['1 day']:.0%} of the portfolio could be sold within a day and "
                           f"{prof['5 days']:.0%} within a week, without trading more than 20% of daily volume. "
                           f"Selling 90% would take about {format_value(d, 'days')}. The slowest holding to sell is "
                           f"{slow['name']} (about {slow['days']:.1f} days)."),
                "sources": [{"label": k, "value": v} for k, v in prof.items()]}

    def _concentration(self, q: str) -> Dict:
        ws = self.ws
        h, n = R.herfindahl(ws.weights)
        top = ws.valuation.head(3)
        names = ", ".join(f"{r['name']} ({r['weight']:.0%})" for _, r in top.iterrows())
        dr = R.diversification_ratio(ws.asset_returns.cov(), ws.weights)
        return {"answer": (f"The three largest holdings are {names}. The portfolio behaves like roughly {n:.0f} "
                           f"equally sized holdings, and diversification cuts its risk by about "
                           f"{1 - 1 / dr:.0%} compared with holding the same assets in isolation."),
                "sources": [{"label": "Effective holdings", "value": n},
                            {"label": "Diversification ratio", "value": dr}]}

    def _compliance(self, q: str) -> Dict:
        s = self.rulebook.summary(self.ws)
        return {"answer": s["plain"], "sources": [{"label": k, "value": v} for k, v in s["counts"].items()]}

    def _private(self, q: str) -> Dict:
        from . import private_assets as PA

        if not self.funds:
            return {"answer": "No private funds have been added to this workspace.", "sources": []}
        v = PA.combined_view(self.ws, self.funds)
        return {"answer": v["plain"], "sources": [{"label": "Private share", "value": v["private_share"]},
                                                  {"label": "Unfunded", "value": v["unfunded_commitments"]}]}

    def _volatility(self, q: str) -> Dict:
        r = self.ws.portfolio_returns
        v, e = R.volatility(r), R.ewma_volatility(r)
        return {"answer": (f"The portfolio's volatility is about {v:.1%} a year: in a typical year its value "
                           f"swings within roughly ±{v:.0%} of where it started. Measured on recent weeks only, it "
                           f"is {e:.1%}."),
                "sources": [{"label": "Volatility", "value": v}, {"label": "EWMA volatility", "value": e}]}

    def _define(self, q: str) -> Dict:
        aliases = {"value at risk": "var_95", "var": "var_95", "expected shortfall": "es_95", "sharpe": "sharpe",
                   "drawdown": "max_drawdown", "beta": "beta", "tracking error": "tracking_error",
                   "duration": "duration", "dv01": "dv01", "tvpi": "tvpi", "dpi": "dpi", "irr": "irr",
                   "volatility": "volatility", "sortino": "sortino", "information ratio": "information_ratio"}
        for word, key in aliases.items():
            if re.search(r"\b" + word + r"\b", q):
                d = GLOSSARY[key]
                return {"answer": f"{d.label}: {d.meaning} (Calculated as: {d.formula}.)", "intent": "define",
                        "sources": []}
        return {"answer": "Which term would you like explained? I know VaR, Sharpe, drawdown, beta, duration, "
                          "TVPI and more.", "sources": []}

    # ------------------------------------------------------------------ helpers
    def _day_contributions(self):
        ws = self.ws
        px = ws.base_prices[ws.held_ids].ffill()
        q = ws.portfolio.quantities()
        return (px.iloc[-1] - px.iloc[-2]) * np.array([q[c] for c in px.columns])

    def _ask_llm(self, question: str) -> Dict:
        system = ("You are a careful investment analytics assistant. Answer ONLY from the JSON facts provided. "
                  "If the facts do not contain the answer, say so. Use plain British English, at most four "
                  "sentences. Never give investment advice or tell the user to buy or sell.")
        prompt = f"Facts:\n{json.dumps(self.facts(), default=str)}\n\nQuestion: {question}"
        try:
            answer = self.llm.complete(system, prompt)
        except Exception as exc:
            answer = f"The language model could not be reached ({exc})."
        return {"answer": answer, "intent": "llm", "engine": "llm", "sources": [{"label": "facts", "value": "snapshot"}]}
