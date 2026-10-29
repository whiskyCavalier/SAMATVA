"""Command-line interface: ``opp <command>``.

    opp demo                 a full tour of every module, printed to the terminal
    opp serve                start the dashboard and API on http://localhost:8000
    opp stream               watch the real-time engine work in the terminal
    opp benchmark            measure raw tick-processing speed
    opp ask "question"       ask the explainer a question
    opp report               write PDF and Excel reports
    opp import FILE.csv      analyse your own holdings file
    opp export-demo FILE     write a self-contained demo dashboard (no server needed)

  Live market data
    opp keys [--test]        show which API keys were found (masked) and optionally test each connection
    opp live --portfolio examples/live_crypto.csv --provider coinbase
                             stream real prices for a real portfolio in the terminal
    opp serve --portfolio examples/live_stocks.csv --provider finnhub
                             the dashboard, driven by a live feed

Keys are read from a .env file in the current folder (copy .env.example) or
from environment variables. They are never printed in full.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from .glossary import format_value
from .workspace import Workspace


def _rule(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


def cmd_demo(args) -> None:
    from .modules import compliance as C, hub, performance as P, private_assets as PA, risk_dashboard as RD
    from .modules import scenarios as S, trading as T
    from .modules.explainer import Explainer

    ws = _workspace(args)
    funds = PA.sample_funds()
    _rule("1. Portfolio hub — what do I own, and what is it worth now?")
    print(hub.describe(ws))
    for k, v in hub.allocation(ws).items():
        print(f"   {k:<14} {v:6.1%}  {'█' * int(round(v * 50))}")
    _rule("2. Risk — how much could I lose, and where is the risk coming from?")
    for m in RD.headline(ws):
        print(f"   {m['label']:<34} {format_value(m['value'], m['fmt']):>10}   {m['meaning']}")
    print("   " + RD.stress_summary_sentence(ws))
    print("   " + RD.bond_analytics(ws)["plain"])
    _rule("3. Scenario lab — what happens if the market moves?")
    for r in S.run_library(ws):
        print(f"   {r['scenario']['name']:<40} {r['pnl_pct']:+7.1%}  {format_value(r['pnl'], 'money'):>10}")
    print("   " + S.monte_carlo_projection(ws)["plain"])
    _rule("4. Performance — am I beating my benchmark, and why?")
    print("   " + P.summary(ws)["plain"])
    print("   " + P.attribution(ws)["plain"])
    _rule("5. Compliance — am I breaking any rules?")
    print("   " + C.default_rulebook().summary(ws)["plain"])
    _rule("6. Trading — what should I trade to reach my target?")
    opt = T.optimise(ws, "risk_parity", max_weight=0.20, min_weight=0.01)
    print("   " + opt["plain"])
    print("   " + T.rebalance(ws, opt["weights"])["plain"])
    _rule("7. Private assets — how do my private investments fit in?")
    for f in funds:
        print("   " + PA.fund_metrics(f)["plain"])
    print("   " + PA.combined_view(ws, funds)["plain"])
    _rule("8. Reports")
    print("   Run 'opp report' to write a PDF and an Excel workbook.")
    _rule("AI explainer — can you explain this to me?")
    ex = Explainer(ws, funds=funds)
    for q in ("Where is my risk coming from?", "What if equities fall 20% and rates rise 1%?"):
        print(f"   Q: {q}\n   A: {ex.ask(q)['answer']}")
    print("\n   Today: " + ex.commentary())


def cmd_serve(args) -> None:
    import os

    import uvicorn

    # The server reads its settings from the environment, so pass the options through it
    if args.provider:
        os.environ["OPP_DATA_PROVIDER"] = args.provider
    if args.portfolio:
        os.environ["OPP_PORTFOLIO_FILE"] = args.portfolio
    if args.benchmark:
        os.environ["OPP_BENCHMARK"] = args.benchmark
    from .config import Settings

    st = Settings.from_env()
    src = f"live prices from {st.provider}" if st.provider != "simulated" else "simulated prices"
    pf = st.portfolio_file or "the sample fund"
    print(f"Open Portfolio Platform on http://{args.host}:{args.port} with {pf} and {src}. "
          "API docs at /docs; press Ctrl+C to stop.")
    uvicorn.run("openportfolio.api.app:app", host=args.host, port=args.port, log_level="warning")


def cmd_stream(args) -> None:
    from .realtime import RealTimeEngine, SimulatedFeed

    ws = _workspace(args)

    async def run():
        feed = SimulatedFeed(ws, ticks_per_second=args.tps, seed=args.seed)
        eng = RealTimeEngine(ws, feed, snapshot_interval=1.0)
        q = eng.subscribe()
        await eng.start()
        print(f"Streaming {args.tps:,.0f} ticks a second for {args.seconds} seconds. "
              f"At second {args.shock_at}, equities fall {abs(args.shock):.0%} in an instant.\n")
        print(f"{'time':>5}  {'NAV':>12}  {'day P/L':>11}  {'VaR 95%':>10}  {'ticks':>9}  {'ticks/s':>8}  {'p99 µs':>7}")
        seen = 0
        for sec in range(1, args.seconds + 1):
            snap = await q.get()
            if sec == args.shock_at:
                feed.inject_shock(args.shock, asset_class="Equity")
            s = snap["stats"]
            print(f"{sec:>4}s  {format_value(snap['nav'], 'money'):>12}  {snap['day_pnl']:>+11,.0f}  "
                  f"{format_value(snap['var95'], 'money'):>10}  {s['ticks']:>9,}  {s['ticks_per_second']:>8,.0f}  "
                  f"{s['p99_us']:>7.1f}")
            new = list(eng.processor.alerts)[seen:]
            new.sort(key=lambda a: a.severity != "critical")
            for a in new[:4]:
                print(f"        {'!!' if a.severity == 'critical' else ' !'} {a.message}")
            if len(new) > 4:
                print(f"           …and {len(new) - 4} more alerts")
            seen = len(eng.processor.alerts)
        await eng.stop()

    asyncio.run(run())


def cmd_keys(args) -> None:
    from .config import PROVIDERS, key_status

    print("API keys (read from the environment and ./.env; shown masked):\n")
    for k in key_status():
        print(f"  {k['var']:<24} {k['value']:<12} {k['unlocks']}")
    print("\nProviders: " + ", ".join(PROVIDERS))
    if not args.test:
        print("\nRun 'opp keys --test' to try a short connection to each provider you have keys for.")
        return
    from .realtime.providers import AlpacaFeed, CoinbaseFeed, FinnhubFeed

    tests = [("coinbase", lambda: CoinbaseFeed({"BTC-USD": "BTC"}, max_retries=0))]
    import os

    if os.environ.get("FINNHUB_API_KEY"):
        tests.append(("finnhub", lambda: FinnhubFeed({"BINANCE:BTCUSDT": "BTC", "AAPL": "AAPL"}, max_retries=0)))
    if os.environ.get("ALPACA_API_KEY_ID") and os.environ.get("ALPACA_API_SECRET_KEY"):
        tests.append(("alpaca-test", lambda: AlpacaFeed({"X": "X"}, feed="test", proxy_instrument="X",
                                                        max_retries=0)))
    print()
    for name, make in tests:
        ok, detail = asyncio.run(_probe(make, args.wait))
        print(f"  {name:<12} {'PASS' if ok else 'FAIL'}  {detail}")


async def _probe(make, wait: float):
    import time as _t

    try:
        feed = make()
    except Exception as exc:
        return False, str(exc)
    t0 = _t.perf_counter()

    async def first():
        async for _ in feed.stream():
            feed.stop()
            return True
        return False

    try:
        got = await asyncio.wait_for(first(), wait)
        return got, f"first price after {_t.perf_counter() - t0:.1f} s"
    except asyncio.TimeoutError:
        return False, (f"connected, but no price within {wait:.0f} s (markets may be closed; "
                       f"status: {feed.status})" if feed.status == "connected" else f"timed out ({feed.status})")
    except Exception as exc:
        return False, str(exc)


def cmd_live(args) -> None:
    from .data.live_universe import build_live_workspace
    from .realtime import AlertConfig, RealTimeEngine
    from .realtime.providers import make_feed

    print(f"Loading {args.portfolio} and its price history…")
    ws = build_live_workspace(args.portfolio, benchmark=args.benchmark)
    feed = make_feed(args.provider, ws)
    stale = args.stale if args.stale is not None else 120.0

    async def run():
        eng = RealTimeEngine(ws, feed, AlertConfig(stale_seconds=stale), snapshot_interval=1.0)
        q = eng.subscribe()
        await eng.start()
        print(f"{hub_line(ws)}\nStreaming live prices from {args.provider} for {args.seconds} seconds "
              f"(opening values are the last daily close). Press Ctrl+C to stop.\n")
        print(f"{'time':>5}  {'NAV':>12}  {'day P/L':>11}  {'VaR 95%':>10}  {'ticks':>7}  {'ticks/s':>8}  "
              f"{'delay ms':>8}  feed")
        seen = 0
        try:
            for sec in range(1, args.seconds + 1):
                snap = await q.get()
                s = snap["stats"]
                print(f"{sec:>4}s  {format_value(snap['nav'], 'money'):>12}  {snap['day_pnl']:>+11,.0f}  "
                      f"{format_value(snap['var95'], 'money'):>10}  {s['ticks']:>7,}  {s['ticks_per_second']:>8,.1f}  "
                      f"{s['feed_delay_ms']:>8.0f}  {snap['source']['status']}")
                new = list(eng.processor.alerts)[seen:]
                for a in sorted(new, key=lambda a: a.severity != "critical")[:4]:
                    print(f"        {'!!' if a.severity == 'critical' else ' !'} {a.message}")
                seen = len(eng.processor.alerts)
                if eng.error:
                    break
        finally:
            await eng.stop()
        print("\nLargest moves since the open:")
        for p in snap["positions"][:5]:
            print(f"  {p['name']:<40} {p['move']:+.2%}  {p['pnl']:+,.0f}")

    asyncio.run(run())


def hub_line(ws) -> str:
    from .modules import hub

    return hub.describe(ws)


def cmd_benchmark(args) -> None:
    from .realtime import benchmark

    print(benchmark(_workspace(args), args.ticks)["plain"])


def cmd_ask(args) -> None:
    from .modules.explainer import Explainer
    from .modules.private_assets import sample_funds

    a = Explainer(_workspace(args), funds=sample_funds()).ask(" ".join(args.question))
    print(a["answer"])


def cmd_report(args) -> None:
    from .modules import reports
    from .modules.private_assets import sample_funds

    ws = _workspace(args)
    t = time.perf_counter()
    print("Wrote", reports.to_pdf(ws, args.pdf, funds=sample_funds()))
    print("Wrote", reports.to_excel(ws, args.xlsx, funds=sample_funds()))
    print(f"in {time.perf_counter() - t:.1f} seconds")


def cmd_import(args) -> None:
    from .data import connectors
    from .modules import compliance as C, hub, risk_dashboard as RD

    base = Workspace.sample()
    pf, rep = connectors.load_holdings(args.file, base.master, args.name)
    print(rep.summary())
    ws = base.with_portfolio(pf)
    print(hub.describe(ws))
    print(RD.stress_summary_sentence(ws))
    print(C.default_rulebook().summary(ws)["plain"])


def cmd_export_demo(args) -> None:
    from .demo import export_html

    print("Wrote", export_html(args.file))


def _workspace(args) -> Workspace:
    return Workspace.sample(getattr(args, "seed", None) or 4)


def main(argv=None) -> None:
    from .config import load_env

    load_env()  # pick up API keys and settings from ./.env, if present
    p = argparse.ArgumentParser(prog="opp", description="Open Portfolio Platform")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="tour every module in the terminal").set_defaults(fn=cmd_demo)
    s = sub.add_parser("serve", help="start the dashboard and API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--provider", help="simulated, coinbase, finnhub, alpaca, alpaca-test or yahoo")
    s.add_argument("--portfolio", help="CSV of real instruments and quantities (see examples/live_stocks.csv)")
    s.add_argument("--benchmark", help="benchmark ticker on Yahoo, e.g. SPY")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("keys", help="show which API keys are set, and test them")
    s.add_argument("--test", action="store_true", help="connect briefly to each provider you have keys for")
    s.add_argument("--wait", type=float, default=20.0, help="seconds to wait for a first price")
    s.set_defaults(fn=cmd_keys)
    s = sub.add_parser("live", help="stream real prices for a real portfolio in the terminal")
    s.add_argument("--portfolio", required=True, help="CSV of real instruments and quantities")
    s.add_argument("--provider", default="coinbase", help="coinbase, finnhub, alpaca, alpaca-test or yahoo")
    s.add_argument("--seconds", type=int, default=60)
    s.add_argument("--benchmark", default=None, help="benchmark ticker on Yahoo, e.g. SPY")
    s.add_argument("--stale", type=float, default=None, help="seconds without a trade before flagging (default 120)")
    s.set_defaults(fn=cmd_live)
    s = sub.add_parser("stream", help="watch the real-time engine in the terminal")
    s.add_argument("--seconds", type=int, default=10)
    s.add_argument("--tps", type=float, default=5000)
    s.add_argument("--shock", type=float, default=-0.04)
    s.add_argument("--shock-at", type=int, default=5)
    s.add_argument("--seed", type=int, default=None)
    s.set_defaults(fn=cmd_stream)
    s = sub.add_parser("benchmark", help="measure tick-processing speed")
    s.add_argument("--ticks", type=int, default=1_000_000)
    s.set_defaults(fn=cmd_benchmark)
    s = sub.add_parser("ask", help="ask the explainer a question")
    s.add_argument("question", nargs="+")
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("report", help="write PDF and Excel reports")
    s.add_argument("--pdf", default="portfolio-report.pdf")
    s.add_argument("--xlsx", default="portfolio-report.xlsx")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("import", help="analyse your own holdings CSV")
    s.add_argument("file")
    s.add_argument("--name", default="My Portfolio")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("export-demo", help="write a self-contained demo dashboard")
    s.add_argument("file")
    s.set_defaults(fn=cmd_export_demo)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
