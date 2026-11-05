#!/usr/bin/env python3
"""PROJECT GODMODE opportunity scoring engine (Mission C).

Reads ``opportunities.toml`` and ``evidence.toml``, validates them, computes
weighted scores with penalties, runs a Monte Carlo sensitivity analysis, and
writes a ranked report.

Design rules
------------
* Zero third-party dependencies (stdlib ``tomllib``), so anyone can re-run it.
* Deterministic: the sensitivity analysis is seeded, so the committed results
  are reproducible byte for byte. ``--check`` fails if they have drifted, which
  is what CI runs.
* Honest about uncertainty: the report states how often each opportunity is
  ranked first or in the top five when every weight and every score is
  perturbed, so a ranking that is within noise is labelled as such.

Usage
-----
    python3 godmode/engine/score.py            # write results/
    python3 godmode/engine/score.py --check    # verify results/ are current
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
_CARDS = ""

DIMENSIONS: tuple[str, ...] = (
    "severity",
    "willingness_to_pay",
    "market",
    "solo_feasibility",
    "ai_leverage",
    "speed_to_mvp",
    "distribution",
    "defensibility",
    "capital_efficiency",
    "expansion",
)

DIMENSION_LABELS: dict[str, str] = {
    "severity": "Problem severity",
    "willingness_to_pay": "Willingness to pay",
    "market": "Market opportunity",
    "solo_feasibility": "Solo-founder feasibility",
    "ai_leverage": "AI automation potential",
    "speed_to_mvp": "Speed to MVP",
    "distribution": "Distribution feasibility",
    "defensibility": "Competitive defensibility",
    "capital_efficiency": "Capital efficiency",
    "expansion": "Long-term expansion",
}

TAG_LABELS: dict[str, str] = {
    "solo_friendly": "Solo-founder friendly",
    "ai_native": "AI-native",
    "high_revenue": "High-revenue potential",
    "low_capital": "Low-capital",
    "fast_to_market": "Fast-to-market",
    "defensible": "Technically defensible",
    "enterprise": "Enterprise-focused",
    "consumer": "Consumer-focused",
    "research_intensive": "Research-intensive",
    "infrastructure": "Long-term infrastructure",
}

MC_ITERATIONS = 4000
MC_SEED = 20261105
SCORE_NOISE = 1.0  # each score perturbed uniformly in [-1, +1], clipped to 1..10
PENALTY_NOISE = 0.25  # each penalty fraction perturbed in [-0.25, +0.25], clipped to 0..1
WEIGHT_CONCENTRATION = 60.0  # Dirichlet concentration around the stated weights


class ValidationError(ValueError):
    """Raised when the dataset violates the scoring contract."""


@dataclass(frozen=True)
class Opportunity:
    oid: str
    name: str
    sector: str
    scores: dict[str, int]
    penalties: dict[str, float]
    tags: tuple[str, ...]
    evidence: tuple[str, ...]
    raw: dict[str, object] = field(repr=False, compare=False)


@dataclass(frozen=True)
class Result:
    opp: Opportunity
    weighted: float
    penalty: float
    final: float
    penalty_breakdown: dict[str, float]


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def load(path: Path) -> dict[str, object]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def validate(data: dict[str, object], evidence_ids: set[str]) -> tuple[
    dict[str, float], dict[str, float], list[Opportunity]
]:
    weights_raw = data.get("weights")
    if not isinstance(weights_raw, dict):
        raise ValidationError("missing [weights] table")
    weights = {k: float(v) for k, v in weights_raw.items()}
    if set(weights) != set(DIMENSIONS):
        raise ValidationError(f"weights must cover exactly {DIMENSIONS}; got {sorted(weights)}")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValidationError(f"weights sum to {sum(weights.values())}, not 1.0")

    catalogue_raw = data.get("penalty_catalogue")
    if not isinstance(catalogue_raw, dict):
        raise ValidationError("missing [penalty_catalogue] table")
    penalty_max = {k: float(v["max"]) for k, v in catalogue_raw.items()}

    opps_raw = data.get("opportunity")
    if not isinstance(opps_raw, list) or not opps_raw:
        raise ValidationError("no [[opportunity]] entries")

    seen: set[str] = set()
    opps: list[Opportunity] = []
    for entry in opps_raw:
        oid = entry.get("id")
        if not isinstance(oid, str) or oid in seen:
            raise ValidationError(f"missing or duplicate id: {oid!r}")
        seen.add(oid)
        scores = entry.get("scores", {})
        if set(scores) != set(DIMENSIONS):
            raise ValidationError(f"{oid}: scores must cover every dimension")
        for dim, value in scores.items():
            if not isinstance(value, int) or not 1 <= value <= 10:
                raise ValidationError(f"{oid}: {dim}={value!r} is not an integer in 1..10")
        penalties = {k: float(v) for k, v in entry.get("penalties", {}).items()}
        for key, frac in penalties.items():
            if key not in penalty_max:
                raise ValidationError(f"{oid}: unknown penalty {key!r}")
            if not 0.0 <= frac <= 1.0:
                raise ValidationError(f"{oid}: penalty {key}={frac} outside 0..1")
        tags = tuple(entry.get("tags", []))
        for tag in tags:
            if tag not in TAG_LABELS:
                raise ValidationError(f"{oid}: unknown tag {tag!r}")
        evidence = tuple(entry.get("evidence", []))
        for eid in evidence:
            if eid not in evidence_ids:
                raise ValidationError(f"{oid}: cites unknown evidence {eid!r}")
        opps.append(
            Opportunity(
                oid=oid,
                name=str(entry["name"]),
                sector=str(entry.get("sector", "")),
                scores=dict(scores),
                penalties=penalties,
                tags=tags,
                evidence=evidence,
                raw=dict(entry),
            )
        )
    return weights, penalty_max, opps


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_one(
    opp: Opportunity,
    weights: dict[str, float],
    penalty_max: dict[str, float],
    scores: dict[str, float] | None = None,
    penalty_fracs: dict[str, float] | None = None,
) -> tuple[float, float, dict[str, float]]:
    s = scores if scores is not None else {k: float(v) for k, v in opp.scores.items()}
    p = penalty_fracs if penalty_fracs is not None else opp.penalties
    weighted = sum(weights[d] * s[d] for d in DIMENSIONS)
    breakdown = {k: p[k] * penalty_max[k] for k in p}
    return weighted, sum(breakdown.values()), breakdown


def rank(
    opps: list[Opportunity], weights: dict[str, float], penalty_max: dict[str, float]
) -> list[Result]:
    results = []
    for opp in opps:
        weighted, penalty, breakdown = score_one(opp, weights, penalty_max)
        results.append(Result(opp, weighted, penalty, weighted - penalty, breakdown))
    # Stable, explicit tie-break: final score, then weighted score, then id.
    results.sort(key=lambda r: (-round(r.final, 6), -round(r.weighted, 6), r.opp.oid))
    return results


def _dirichlet(rng: random.Random, alphas: list[float]) -> list[float]:
    draws = [rng.gammavariate(a, 1.0) for a in alphas]
    total = sum(draws)
    return [d / total for d in draws]


def sensitivity(
    opps: list[Opportunity], weights: dict[str, float], penalty_max: dict[str, float]
) -> dict[str, dict[str, float]]:
    """Monte Carlo: perturb weights, scores and penalties together."""
    rng = random.Random(MC_SEED)
    alphas = [weights[d] * WEIGHT_CONCENTRATION for d in DIMENSIONS]
    first = dict.fromkeys((o.oid for o in opps), 0)
    top5 = dict.fromkeys((o.oid for o in opps), 0)
    ranks: dict[str, list[int]] = {o.oid: [] for o in opps}
    for _ in range(MC_ITERATIONS):
        w = dict(zip(DIMENSIONS, _dirichlet(rng, alphas), strict=True))
        finals: list[tuple[float, str]] = []
        for opp in opps:
            s = {
                d: min(10.0, max(1.0, v + rng.uniform(-SCORE_NOISE, SCORE_NOISE)))
                for d, v in opp.scores.items()
            }
            p = {
                k: min(1.0, max(0.0, v + rng.uniform(-PENALTY_NOISE, PENALTY_NOISE)))
                for k, v in opp.penalties.items()
            }
            weighted, penalty, _ = score_one(opp, w, penalty_max, s, p)
            finals.append((weighted - penalty, opp.oid))
        finals.sort(key=lambda t: (-t[0], t[1]))
        for position, (_, oid) in enumerate(finals, start=1):
            ranks[oid].append(position)
            if position == 1:
                first[oid] += 1
            if position <= 5:
                top5[oid] += 1
    return {
        oid: {
            "p_first": first[oid] / MC_ITERATIONS,
            "p_top5": top5[oid] / MC_ITERATIONS,
            "median_rank": float(statistics.median(ranks[oid])),
            "p90_rank": float(sorted(ranks[oid])[int(0.9 * MC_ITERATIONS) - 1]),
        }
        for oid in first
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def render_markdown(
    results: list[Result],
    sens: dict[str, dict[str, float]],
    weights: dict[str, float],
    penalty_max: dict[str, float],
    catalogue: dict[str, dict[str, object]],
) -> str:
    lines: list[str] = []
    add = lines.append
    add("# Opportunity ranking (generated)")
    add("")
    add("> Generated by `godmode/engine/score.py` from `opportunities.toml`. Do not edit by hand;")
    add("> change the dataset and re-run. Scores are analyst judgments. The sensitivity columns show")
    add(f"> how often each opportunity ranks first / top-5 across {MC_ITERATIONS:,} runs in which every")
    add(f"> weight (Dirichlet, concentration {WEIGHT_CONCENTRATION:g}), every score (±{SCORE_NOISE:g})")
    add(f"> and every penalty (±{PENALTY_NOISE:g}) is perturbed at once (seed {MC_SEED}).")
    add("")
    add("## Weights")
    add("")
    add("| Dimension | Weight |")
    add("|---|---|")
    for d in DIMENSIONS:
        add(f"| {DIMENSION_LABELS[d]} | {weights[d]:.0%} |")
    add("")
    add("## Penalty catalogue")
    add("")
    add("| Penalty | Max deduction | Applies when |")
    add("|---|---|---|")
    for key, spec in catalogue.items():
        add(f"| `{key}` | {penalty_max[key]:.2f} | {spec['description']} |")
    add("")
    add("## Full ranking")
    add("")
    add("| # | ID | Opportunity | Weighted | Penalty | **Final** | P(#1) | P(top-5) | Median rank |")
    add("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(results, start=1):
        s = sens[r.opp.oid]
        add(
            f"| {i} | {r.opp.oid} | {r.opp.name} | {_fmt(r.weighted)} | "
            f"−{_fmt(r.penalty)} | **{_fmt(r.final)}** | {s['p_first']:.0%} | "
            f"{s['p_top5']:.0%} | {s['median_rank']:.0f} |"
        )
    add("")
    add("## Top 10 — score breakdown")
    add("")
    header = "| ID | " + " | ".join(DIMENSION_LABELS[d].split()[0] for d in DIMENSIONS) + " | Penalties |"
    add(header)
    add("|" + "---|" * (len(DIMENSIONS) + 2))
    for r in results[:10]:
        pen = ", ".join(f"{k} −{v:.2f}" for k, v in sorted(r.penalty_breakdown.items())) or "—"
        cells = " | ".join(str(r.opp.scores[d]) for d in DIMENSIONS)
        add(f"| {r.opp.oid} | {cells} | {pen} |")
    add("")
    add("## Classification")
    add("")
    add("| Classification | Opportunities (ranked) |")
    add("|---|---|")
    for tag, label in TAG_LABELS.items():
        members = [r.opp.oid for r in results if tag in r.opp.tags]
        add(f"| {label} | {', '.join(members) if members else '—'} |")
    add("")
    add("## Selection")
    add("")
    top = results[0]
    runner = results[1]
    margin = top.final - runner.final
    add(f"* **Recommended first venture:** {top.opp.oid} — {top.opp.name} "
        f"(final {_fmt(top.final)}, ranked first in {sens[top.opp.oid]['p_first']:.0%} of perturbed runs).")
    add(f"* **Margin over #2:** {_fmt(margin)} points.")
    add("* **Finalists:** " + ", ".join(f"{r.opp.oid} ({_fmt(r.final)})" for r in results[:3]) + ".")
    add("* **Top five (red-teamed in the dossier):** "
        + ", ".join(r.opp.oid for r in results[:5]) + ".")
    close = [
        r.opp.oid for r in results[1:6]
        if sens[r.opp.oid]["p_top5"] < 0.8
    ]
    if close:
        add("* **Within noise:** " + ", ".join(close)
            + " — their order changes under perturbation; choose between them on strategic fit, not score.")
    add("")
    return "\n".join(lines)


CARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("concept", "Concept"),
    ("customer", "Customer"),
    ("problem", "Painful problem"),
    ("alternatives", "Existing alternatives"),
    ("gap", "Why alternatives fall short"),
    ("solution", "Proposed solution"),
    ("technology", "Required technology"),
    ("mvp", "MVP scope"),
    ("complexity", "Development complexity"),
    ("capital", "Initial capital"),
    ("pricing", "Pricing model"),
    ("acquisition", "Customer acquisition"),
    ("competition", "Competitive landscape"),
    ("defensibility_note", "Technical defensibility"),
    ("regulatory", "Regulatory / security concerns"),
    ("main_risk", "Main failure risk"),
    ("evidence_needed", "Evidence required before investing"),
)


def render_cards(results: list[Result], evidence: dict[str, dict[str, object]]) -> str:
    """Mission A: the full 17-field card for every opportunity, in ranked order."""
    lines = [
        "# Mission A — 50 opportunities in full (generated)",
        "",
        "> Generated by `godmode/engine/score.py` from `opportunities.toml`, in ranked order.",
        "> Evidence IDs refer to `evidence.toml`; an opportunity without evidence IDs rests on",
        "> analyst judgment alone and says so. Scores are judgments, stress-tested in `ranking.md`.",
        "",
    ]
    for i, r in enumerate(results, start=1):
        o = r.opp.raw
        tags = ", ".join(TAG_LABELS[t] for t in r.opp.tags) or "—"
        lines += [f"## {i}. {r.opp.oid} — {r.opp.name}", ""]
        lines.append(
            f"**Final score {r.final:.2f}** (weighted {r.weighted:.2f}, penalties −{r.penalty:.2f}) · "
            f"sector `{r.opp.sector}` · {tags}"
        )
        lines.append("")
        for key, label in CARD_FIELDS:
            if key in o:
                lines.append(f"- **{label}:** {o[key]}")
        cites = []
        for eid in r.opp.evidence:
            ev = evidence.get(eid, {})
            cites.append(f"{eid} ({ev.get('strength', '?')})")
        lines.append(f"- **Evidence:** {', '.join(cites) if cites else 'none yet — judgment only'}")
        lines.append(f"- **Why this score:** {o.get('rationale', '')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def build(here: Path = HERE) -> tuple[str, str]:
    evidence = load(here / "evidence.toml")
    evidence_ids = {e["id"] for e in evidence.get("evidence", [])}
    data = load(here / "opportunities.toml")
    weights, penalty_max, opps = validate(data, evidence_ids)
    results = rank(opps, weights, penalty_max)
    sens = sensitivity(opps, weights, penalty_max)
    catalogue = data["penalty_catalogue"]
    assert isinstance(catalogue, dict)
    md = render_markdown(results, sens, weights, penalty_max, catalogue)
    global _CARDS
    _CARDS = render_cards(results, {e["id"]: e for e in evidence.get("evidence", [])})
    payload = {
        "generated_from": ["opportunities.toml", "evidence.toml"],
        "monte_carlo": {
            "iterations": MC_ITERATIONS,
            "seed": MC_SEED,
            "score_noise": SCORE_NOISE,
            "penalty_noise": PENALTY_NOISE,
            "weight_concentration": WEIGHT_CONCENTRATION,
        },
        "ranking": [
            {
                "rank": i,
                "id": r.opp.oid,
                "name": r.opp.name,
                "sector": r.opp.sector,
                "weighted": round(r.weighted, 4),
                "penalty": round(r.penalty, 4),
                "final": round(r.final, 4),
                "penalties": {k: round(v, 4) for k, v in r.penalty_breakdown.items()},
                "tags": list(r.opp.tags),
                "evidence": list(r.opp.evidence),
                "sensitivity": {k: round(v, 4) for k, v in sens[r.opp.oid].items()},
            }
            for i, r in enumerate(results, start=1)
        ],
    }
    return md, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if results/ are stale")
    args = parser.parse_args(argv)
    try:
        md, js = build()
    except ValidationError as exc:
        print(f"dataset invalid: {exc}", file=sys.stderr)
        return 2
    md_path, js_path = RESULTS / "ranking.md", RESULTS / "ranking.json"
    cards_path = RESULTS / "opportunities.md"
    outputs = ((md_path, md), (js_path, js), (cards_path, _CARDS))
    if args.check:
        stale = [
            p.name for p, content in outputs
            if not p.exists() or p.read_text(encoding="utf-8") != content
        ]
        if stale:
            print(f"stale results: {', '.join(stale)} — run score.py and commit", file=sys.stderr)
            return 1
        print("results are current")
        return 0
    RESULTS.mkdir(parents=True, exist_ok=True)
    for path, content in outputs:
        path.write_text(content, encoding="utf-8")
    print("wrote", ", ".join(str(p.relative_to(HERE.parent.parent)) for p, _ in outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
