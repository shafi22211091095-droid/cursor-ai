#!/usr/bin/env python3
"""PROJECT GODMODE — LabelLedger financial model.

A monthly, expected-value simulation driven entirely by ``assumptions.toml``.
No third-party dependencies; deterministic; ``--check`` verifies the committed
results are current (CI runs it).

Funnel: outbound prospects -> demos -> paid, plus digest subscribers -> paid.
New customers split across plans by the scenario mix; customers won in the
first six months pay a pilot price. Churn removes a fraction of customers
(and their MRR) each month; expansion grows retained MRR. Costs: payment
fees, LLM tokens, hosting, tools, accounting, marketing, hires, one-offs and a
founder draw (treated as a cash cost, because runway is what it pays for).

Usage:
    python3 godmode/finance/model.py
    python3 godmode/finance/model.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
PILOT_MONTHS = 6


@dataclass
class Month:
    month: int
    new_customers: float
    customers: float
    mrr: float
    services: float
    revenue: float
    cost_of_revenue: float
    opex: float
    total_costs: float
    net: float
    cash: float


def _prospects(s: dict[str, Any], m: int) -> float:
    """Outbound prospects contacted in month ``m`` (ramping up after the start month)."""
    start = s["sales_start_month"]
    if m < start:
        return 0.0
    ramp = max(1, s["outbound_ramp_months"])
    return float(s["outbound_prospects"]) * min(1.0, (m - start + 1) / ramp)


def simulate(a: dict[str, Any], s: dict[str, Any]) -> list[Month]:
    plans, costs, months = a["plans"], a["costs"], a["meta"]["months"]
    list_arpa = sum(plans[p] * s["mix"][p] for p in ("pro", "team", "enterprise"))
    one_off = {int(k): float(v) for k, v in costs["one_off"].items()}
    cash = float(a["meta"]["starting_cash"])
    customers = 0.0
    mrr = 0.0
    rows: list[Month] = []
    for m in range(1, months + 1):
        outbound = _prospects(s, m - s["sales_cycle_months"]) * s["reply_to_demo"] * s["demo_to_paid"]
        live = m - s["digest_start_month"] + 1
        subscribers = s["digest_subscribers_per_month"] * max(0, live)
        # subscribers convert only after a full sales cycle on the list
        inbound = subscribers * s["digest_monthly_conversion"] if live > s["sales_cycle_months"] else 0.0
        new = outbound + inbound
        price_factor = s["pilot_price_factor"] if m <= PILOT_MONTHS else 1.0
        churned = customers * s["monthly_churn"]
        mrr = mrr * (1 - s["monthly_churn"]) * (1 + s["monthly_expansion"]) + new * list_arpa * price_factor
        customers = customers - churned + new
        pk = s["services_packages"]
        services = pk["per_month"] * pk["price"] if m <= pk["months"] else 0.0
        revenue = mrr + services
        invoices = customers + (pk["per_month"] if m <= pk["months"] else 0.0)
        cost_of_revenue = (
            revenue * costs["merchant_of_record_pct"]
            + invoices * costs["merchant_fixed_per_invoice"]
            + customers * (costs["llm_cost_per_customer"] + costs["hosting_per_customer"])
            + costs["llm_fixed"]
            + costs["hosting_base"]
        )
        marketing = s["marketing"]["amount"] if m >= s["marketing"]["from_month"] else 0.0
        hires = sum(h["monthly"] for h in s["hires"] if m >= h["from_month"])
        opex = (
            costs["tools"]
            + costs["accounting_legal_monthly"]
            + marketing
            + hires
            + s["founder_draw"]
            + one_off.get(m, 0.0)
        )
        total = cost_of_revenue + opex
        net = revenue - total
        cash += net
        rows.append(Month(m, new, customers, mrr, services, revenue, cost_of_revenue, opex, total, net, cash))
    return rows


def unit_economics(a: dict[str, Any], s: dict[str, Any], rows: list[Month]) -> dict[str, float | None]:
    plans, costs = a["plans"], a["costs"]
    arpa = sum(plans[p] * s["mix"][p] for p in ("pro", "team", "enterprise"))
    var_cost = arpa * costs["merchant_of_record_pct"] + costs["merchant_fixed_per_invoice"] + costs[
        "llm_cost_per_customer"
    ] + costs["hosting_per_customer"]
    gm = (arpa - var_cost) / arpa
    churn = s["monthly_churn"]
    ltv = arpa * gm / churn
    year1 = rows[:12]
    acquired = sum(r.new_customers for r in year1)
    marketing_spend = sum(
        s["marketing"]["amount"] for r in year1 if r.month >= s["marketing"]["from_month"]
    )
    ft = a["founder_time"]
    prospects = sum(_prospects(s, r.month) for r in year1)
    demos = prospects * s["reply_to_demo"]
    founder_cost = (prospects * ft["hours_per_prospect"] + demos * ft["hours_per_demo"]) * ft["hour_value"]
    cash_cac = marketing_spend / acquired if acquired else None
    loaded_cac = (marketing_spend + founder_cost) / acquired if acquired else None
    breakeven = next((r.month for r in rows if r.net >= 0 and r.month > 1), None)
    low = min(r.cash for r in rows)
    return {
        "arpa_list": round(arpa, 2),
        "gross_margin": round(gm, 4),
        "monthly_churn": churn,
        "expected_lifetime_months": round(1 / churn, 1),
        "ltv": round(ltv, 0),
        "cash_cac_year1": round(cash_cac, 2) if cash_cac is not None else None,
        "ltv_to_cash_cac": round(ltv / cash_cac, 1) if cash_cac else None,
        "loaded_cac_year1": round(loaded_cac, 2) if loaded_cac is not None else None,
        "ltv_to_loaded_cac": round(ltv / loaded_cac, 1) if loaded_cac else None,
        "founder_sales_hours_year1": round(
            prospects * ft["hours_per_prospect"] + demos * ft["hours_per_demo"], 0
        ),
        "customers_month_12": round(rows[11].customers, 1),
        "breakeven_month": breakeven,
        "lowest_cash": round(low, 0),
        "external_cash_needed": round(max(0.0, -low), 0),
    }


def sensitivity_grid(a: dict[str, Any]) -> list[list[str]]:
    """Base-case month-12 MRR when price and close rate move together."""
    base = a["scenario"]["base"]
    header = ["Close rate \\ Price", "−20%", "List", "+20%"]
    grid = [header]
    for close in (0.15, 0.20, 0.25):
        row = [f"{close:.0%}"]
        for factor in (0.8, 1.0, 1.2):
            trial = json.loads(json.dumps(a))
            trial["plans"] = {k: v * factor for k, v in a["plans"].items()}
            s = {**base, "demo_to_paid": close}
            rows = simulate(trial, s)
            row.append(f"${rows[11].mrr:,.0f}")
        grid.append(row)
    return grid


def money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def render(a: dict[str, Any]) -> tuple[str, str]:
    out: list[str] = []
    add = out.append
    payload: dict[str, Any] = {"assumptions_file": "assumptions.toml", "scenarios": {}}
    add("# LabelLedger — financial model (generated)")
    add("")
    add("> Generated by `godmode/finance/model.py` from `assumptions.toml`. **Every input is an")
    add("> assumption** to be replaced by evidence from customer discovery and pilots. These are")
    add("> scenarios, not forecasts, and nothing here is guaranteed.")
    add("")
    snapshots = (6, 12, 24, 36)
    for key in ("conservative", "base", "aggressive"):
        s = a["scenario"][key]
        rows = simulate(a, s)
        ue = unit_economics(a, s, rows)
        payload["scenarios"][key] = {
            "label": s["label"],
            "unit_economics": ue,
            "months": [{k: round(v, 2) if isinstance(v, float) else v for k, v in asdict(r).items()} for r in rows],
        }
        add(f"## {s['label']}")
        add("")
        add("| Month | Customers | MRR | ARR run-rate | Revenue (incl. services) | Total costs | Net | Cash |")
        add("|---|---|---|---|---|---|---|---|")
        for m in snapshots:
            r = rows[m - 1]
            add(
                f"| {m} | {r.customers:.0f} | {money(r.mrr)} | {money(r.mrr * 12)} | {money(r.revenue)} | "
                f"{money(r.total_costs)} | {money(r.net)} | {money(r.cash)} |"
            )
        add("")
        be = ue["breakeven_month"]
        add(
            f"Break-even (monthly net ≥ 0, founder draw included): **{'month ' + str(be) if be else 'not within 36 months'}** · "
            f"lowest cash balance {money(float(ue['lowest_cash'] or 0))} → external cash needed "
            f"**{money(float(ue['external_cash_needed'] or 0))}** · list ARPA {money(float(ue['arpa_list'] or 0))}/mo · "
            f"gross margin {float(ue['gross_margin'] or 0):.0%} · LTV {money(float(ue['ltv'] or 0))}"
            + (f" · cash CAC (yr 1) {money(float(ue['cash_cac_year1']))}" if ue["cash_cac_year1"] else "")
            + (
                f" · CAC incl. founder time {money(float(ue['loaded_cac_year1']))}"
                f" (LTV/CAC {ue['ltv_to_loaded_cac']}x; {ue['founder_sales_hours_year1']:.0f} founder sales hours in year 1)"
                if ue["loaded_cac_year1"]
                else ""
            )
        )
        add("")
    add("## Sensitivity — base case, month-12 MRR")
    add("")
    grid = sensitivity_grid(a)
    add("| " + " | ".join(grid[0]) + " |")
    add("|" + "---|" * len(grid[0]))
    for row in grid[1:]:
        add("| " + " | ".join(row) + " |")
    add("")
    add("## What would break this model")
    add("")
    add("1. **Close rate.** Every scenario assumes buyers convert after a demo. If the first 30 demos")
    add("   convert below 10%, the base case moves to the conservative case — re-price or re-position.")
    add("2. **Churn.** At 5%/month, lifetime falls to 20 months and LTV roughly halves versus base.")
    add("3. **Founder draw.** It is the largest cost line in year 1. It is what the runway buys;")
    add("   lowering it extends runway but not the business case.")
    add("4. **Payment rails.** Selling from Bangladesh depends on a merchant of record or a foreign")
    add("   entity; fees and payout friction are modelled at 5% + $0.50 and must be verified.")
    add("5. **Services dependency.** Early revenue includes concierge packages that consume founder")
    add("   time; if they do not convert into subscriptions, they are a consultancy, not a product.")
    payload["sensitivity_month12_mrr"] = grid
    return "\n".join(out) + "\n", json.dumps(payload, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LabelLedger financial model")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    with (HERE / "assumptions.toml").open("rb") as fh:
        assumptions = tomllib.load(fh)
    md, js = render(assumptions)
    targets = {RESULTS / "model.md": md, RESULTS / "model.json": js}
    if args.check:
        stale = [p.name for p, c in targets.items() if not p.exists() or p.read_text(encoding="utf-8") != c]
        if stale:
            print(f"stale: {', '.join(stale)} — run model.py and commit", file=sys.stderr)
            return 1
        print("financial model results are current")
        return 0
    RESULTS.mkdir(parents=True, exist_ok=True)
    for path, content in targets.items():
        path.write_text(content, encoding="utf-8")
    print("wrote", ", ".join(str(p.relative_to(HERE.parent.parent)) for p in targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
