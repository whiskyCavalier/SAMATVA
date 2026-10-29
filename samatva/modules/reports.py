"""Module 8 — Reports.

The simple question it answers: *how do I show this to my board or clients?*

One call gathers every module's results into a single report pack, which can
be written as a PDF (for reading) or an Excel workbook (for working with the
numbers). Every figure in the PDF carries its plain-English meaning.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from ..glossary import format_value
from ..workspace import Workspace
from . import compliance as C
from . import hub
from . import performance as P
from . import private_assets as PA
from . import risk_dashboard as RD
from . import scenarios as S
from .explainer import Explainer


def build(ws: Workspace, rulebook: Optional[C.RuleBook] = None, funds: Optional[List[PA.PrivateFund]] = None) -> Dict:
    """Collect every section of the report into one dictionary."""
    rulebook = rulebook or C.default_rulebook()
    funds = funds if funds is not None else []
    ex = Explainer(ws, rulebook, funds)
    data = {
        "generated": datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC"),
        "summary": hub.summary(ws),
        "description": hub.describe(ws),
        "commentary": ex.commentary(),
        "holdings": hub.holdings(ws),
        "allocation": {d: hub.allocation(ws, d).to_dict() for d in ("asset_class", "sector", "country", "currency")},
        "risk": RD.report(ws),
        "scenarios": S.run_library(ws),
        "projection": S.monte_carlo_projection(ws),
        "performance": P.summary(ws),
        "attribution": P.attribution(ws) if ws.market.benchmark_segments else None,
        "compliance": rulebook.summary(ws),
        "anomalies": ex.anomalies(),
        "private": [PA.fund_metrics(f) for f in funds],
        "combined": PA.combined_view(ws, funds) if funds else None,
        "growth": P.growth_series(ws),
    }
    return data


# --------------------------------------------------------------------------- Excel
def to_excel(ws: Workspace, path, rulebook=None, funds=None) -> str:
    d = build(ws, rulebook, funds)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        s = d["summary"]
        pd.DataFrame([
            ("Portfolio", s["portfolio"]), ("As of", s["as_of"]), ("Net asset value", s["nav"]["value"]),
            ("Cash", s["cash"]), ("Holdings", s["holdings"]), ("Day change", s["day_change"]),
            ("Commentary", d["commentary"]),
        ], columns=["Item", "Value"]).to_excel(xl, "Summary", index=False)
        pd.DataFrame(d["holdings"]).to_excel(xl, "Holdings", index=False)
        alloc = pd.concat({k: pd.Series(v) for k, v in d["allocation"].items()}).rename("weight")
        alloc.reset_index().rename(columns={"level_0": "dimension", "level_1": "group"}).to_excel(
            xl, "Allocation", index=False)
        pd.DataFrame(d["risk"]["headline"])[["label", "value", "meaning", "formula"]].to_excel(
            xl, "Risk", index=False)
        pd.DataFrame(d["risk"]["contributions"]).to_excel(xl, "Risk contributions", index=False)
        pd.DataFrame([{"scenario": r["scenario"]["name"], "pnl": r["pnl"], "pnl_pct": r["pnl_pct"],
                       "explanation": r["plain"]} for r in d["scenarios"]]).to_excel(xl, "Scenarios", index=False)
        pd.DataFrame(d["performance"]["metrics"])[["label", "value", "meaning"]].to_excel(
            xl, "Performance", index=False)
        d["growth"].to_excel(xl, "Growth of 100")
        if d["attribution"]:
            pd.DataFrame(d["attribution"]["table"]).to_excel(xl, "Attribution", index=False)
        pd.DataFrame(d["compliance"]["results"]).to_excel(xl, "Compliance", index=False)
        if d["private"]:
            pd.DataFrame([{k: (v["value"] if isinstance(v, dict) else v) for k, v in f.items()}
                          for f in d["private"]]).to_excel(xl, "Private assets", index=False)
        for sheet in xl.book.worksheets:
            for col in sheet.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:50])
                sheet.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
    return str(path)


# --------------------------------------------------------------------------- PDF
def _chart_png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    buf.seek(0)
    return buf


def _charts(d: Dict):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, accent, grey = "#1f2a37", "#2f6fdf", "#9aa5b1"
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": grey, "axes.labelcolor": ink, "xtick.color": ink, "ytick.color": ink})
    out = {}

    g = d["growth"]
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    ax.plot(g.index, g["Portfolio"], color=accent, lw=1.6, label="Portfolio")
    if "Benchmark" in g:
        ax.plot(g.index, g["Benchmark"], color=grey, lw=1.2, label="Benchmark")
    ax.set_ylabel("Growth of 100")
    ax.legend(frameon=False, loc="upper left")
    ax.grid(axis="y", color="#e5e9ef")
    out["growth"] = _chart_png(fig)
    plt.close(fig)

    a = pd.Series(d["allocation"]["asset_class"]).sort_values()
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.barh(a.index, a.values * 100, color=accent)
    for i, v in enumerate(a.values):
        ax.text(v * 100 + 0.8, i, f"{v:.0%}", va="center", fontsize=8, color=ink)
    ax.set_xlabel("% of portfolio")
    ax.set_xlim(0, a.max() * 100 * 1.25)
    out["allocation"] = _chart_png(fig)
    plt.close(fig)

    sc = pd.Series({r["scenario"]["name"].split(" (")[0]: r["pnl_pct"] * 100 for r in d["scenarios"]}).sort_values()
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    colors = ["#c0392b" if v < 0 else "#2e8b57" for v in sc.values]
    ax.barh(sc.index, sc.values, color=colors)
    ax.axvline(0, color=grey, lw=0.8)
    ax.set_xlabel("Change in value, %")
    out["scenarios"] = _chart_png(fig)
    plt.close(fig)
    return out


def to_pdf(ws: Workspace, path, rulebook=None, funds=None) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    d = build(ws, rulebook, funds)
    charts = _charts(d)
    ss = getSampleStyleSheet()
    ink = colors.HexColor("#1f2a37")
    accent = colors.HexColor("#2f6fdf")
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9.5, leading=13.5, textColor=ink)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#52606d"))
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=20, leading=24, alignment=0, textColor=ink)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, leading=16, textColor=accent, spaceBefore=10)

    def table(rows, widths, header=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        style = [("FONTSIZE", (0, 0), (-1, -1), 8.5), ("TEXTCOLOR", (0, 0), (-1, -1), ink),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25,
                                                       colors.HexColor("#e5e9ef")),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if header:
            style += [("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                      ("LINEBELOW", (0, 0), (-1, 0), 0.8, accent)]
        t.setStyle(TableStyle(style))
        return t

    s = d["summary"]
    story = [
        Paragraph(f"{s['portfolio']}", h1),
        Paragraph(f"Portfolio report as of {s['as_of']} &middot; generated {d['generated']}", small),
        Spacer(1, 6 * mm),
        Paragraph("In brief", h2),
        Paragraph(d["description"], body), Spacer(1, 2 * mm),
        Paragraph(d["commentary"], body),
        Spacer(1, 3 * mm),
        Table([[Image(charts["allocation"], width=82 * mm, height=58 * mm),
                Image(charts["scenarios"], width=86 * mm, height=58 * mm)]], colWidths=[88 * mm, 90 * mm]),
        Paragraph("Left: where the money is. Right: how the portfolio would fare in each stress scenario.", small),
        Paragraph("Key risk measures", h2),
    ]
    rows = [["Measure", "Value", "What it means"]]
    for m in d["risk"]["headline"]:
        val = format_value(m["value"], m["fmt"])
        if m.get("money") is not None:
            val += f" ({format_value(m['money'], 'money')})"
        rows.append([Paragraph(m["label"], body), val, Paragraph(m["meaning"], small)])
    story += [table(rows, [48 * mm, 36 * mm, 94 * mm]), Spacer(1, 2 * mm),
              Paragraph(d["risk"]["summary"], body), Paragraph(d["risk"]["bonds"].get("plain", ""), body)]

    rc = d["risk"]["contributions"][:6]
    rows = [["Holding", "Share of money", "Share of risk"]] + [
        [r["name"], f"{r['weight']:.1%}", f"{r['percent']:.1%}"] for r in rc]
    story += [Paragraph("Where the risk comes from", h2), table(rows, [90 * mm, 40 * mm, 40 * mm])]

    story += [Paragraph("Performance", h2), Image(charts["growth"], width=178 * mm, height=64 * mm)]
    perf = d["performance"]
    if perf.get("plain"):
        story.append(Paragraph(perf["plain"], body))
    pr = perf["period_returns"]
    bpr = perf.get("benchmark_period_returns", {})
    rows = [["Period", "Portfolio", "Benchmark"]] + [
        [k, format_value(v, "pct"), format_value(bpr.get(k), "pct")] for k, v in pr.items()]
    story += [Spacer(1, 2 * mm), table(rows, [60 * mm, 40 * mm, 40 * mm])]
    if d["attribution"]:
        story += [Spacer(1, 2 * mm), Paragraph(d["attribution"]["plain"], body)]

    story += [Paragraph("Stress scenarios", h2)]
    rows = [["Scenario", "Change", "In money"]] + [
        [Paragraph(r["scenario"]["name"], body), f"{r['pnl_pct']:+.1%}", format_value(r["pnl"], "money")]
        for r in d["scenarios"]]
    story += [table(rows, [100 * mm, 30 * mm, 40 * mm]), Spacer(1, 2 * mm),
              Paragraph(d["projection"]["plain"], body),
              Paragraph("Scenario moves are illustrative approximations of past episodes, not forecasts.", small)]

    comp = d["compliance"]
    story += [Paragraph("Compliance", h2), Paragraph(comp["plain"], body)]
    if comp["issues"]:
        rows = [["Status", "Detail"]] + [[i["status"].title(), Paragraph(i["message"], body)] for i in comp["issues"]]
        story.append(table(rows, [25 * mm, 150 * mm]))

    if d["private"]:
        story += [Paragraph("Private assets", h2)]
        rows = [["Fund", "Paid in", "NAV", "TVPI", "IRR"]] + [
            [f["name"], format_value(f["paid_in"], "money"), format_value(f["nav"], "money"),
             format_value(f["tvpi"]["value"], "ratio"), format_value(f["irr"]["value"], "pct")] for f in d["private"]]
        story += [table(rows, [70 * mm, 28 * mm, 28 * mm, 20 * mm, 20 * mm]), Spacer(1, 2 * mm),
                  Paragraph(d["combined"]["plain"], body)]

    story += [PageBreak(), Paragraph("Holdings", h2)]
    rows = [["Holding", "Class", "Value", "Weight"]] + [
        [Paragraph(h["name"], body), h["asset_class"], format_value(h["market_value"], "money"), f"{h['weight']:.1%}"]
        for h in d["holdings"]]
    story += [table(rows, [80 * mm, 35 * mm, 35 * mm, 25 * mm]), Spacer(1, 6 * mm),
              KeepTogether([Paragraph("Important information", h2), Paragraph(
                  "This report is produced by analytics software. It describes the portfolio and estimates risk "
                  "from historical data and stated assumptions. It is not investment advice, and past behaviour "
                  "does not guarantee future results.", small)])]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#7b8794"))
        canvas.drawString(18 * mm, 10 * mm, f"{s['portfolio']} · Samatva")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=f"{s['portfolio']} report", author="Samatva")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return str(path)
