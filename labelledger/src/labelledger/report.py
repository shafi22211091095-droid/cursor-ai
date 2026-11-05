"""Render diffs and drift reports as HTML (standalone or in-app), Markdown and JSON."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__
from .ai import SummaryResult
from .diff import LabelDiff
from .drift import DriftReport
from .severity import Severity

WEB = Path(__file__).parent / "web"
TEMPLATES = WEB / "templates"
CSS_PATH = WEB / "static" / "style.css"


@lru_cache(maxsize=1)
def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["engine_version"] = __version__
    return env


@lru_cache(maxsize=1)
def css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def render_diff_html(diff: LabelDiff, summary: SummaryResult | None = None) -> str:
    """A self-contained HTML report (inline CSS, no scripts, no external requests)."""
    return (
        environment()
        .get_template("report.html")
        .render(
            kind="diff",
            title=f"Redline — {diff.new.title}",
            diff=diff,
            summary=summary,
            css=css(),
            generated_at=diff.generated_at,
            sha_a=diff.old.sha256,
            sha_b=diff.new.sha256,
        )
    )


def render_drift_html(report: DriftReport, *, min_severity: Severity = Severity.MINOR) -> str:
    hidden = sum(1 for f in report.findings if f.severity < min_severity)
    return (
        environment()
        .get_template("report.html")
        .render(
            kind="drift",
            title=f"Drift — {report.follower.title}",
            report=report,
            min_severity=int(min_severity),
            hidden_count=hidden,
            css=css(),
            generated_at=report.generated_at,
            sha_a=report.reference.sha256,
            sha_b=report.follower.sha256,
        )
    )


def diff_to_json(diff: LabelDiff, summary: SummaryResult | None = None) -> str:
    payload: dict[str, Any] = diff.to_dict()
    if summary is not None:
        payload["summary"] = summary.to_dict()
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def drift_to_json(report: DriftReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"


def _md_segments(segments: list[Any]) -> str:
    """Markdown redline: insertions **bold**, deletions ~~struck~~, spacing kept outside markers."""
    out: list[str] = []
    previous = ""
    for s in segments:
        text = s.text.replace("~~", "").replace("**", "")
        core = text.strip()
        if s.op == "eq" or not core:
            out.append(text)
            previous = s.op
            continue
        lead = text[: len(text) - len(text.lstrip())]
        if s.op == "ins" and previous == "del" and not lead:
            lead = " "  # keep "~~old~~ **new**" readable when the edit replaced a word in place
        mark = "**" if s.op == "ins" else "~~"
        out.append(f"{lead}{mark}{core}{mark}")
        previous = s.op
    return "".join(out)


def diff_to_markdown(diff: LabelDiff, summary: SummaryResult | None = None) -> str:
    lines: list[str] = []
    add = lines.append
    span = (
        f"v{diff.old.version} → v{diff.new.version}" if diff.same_set else "cross-label comparison"
    )
    add(f"# Redline: {diff.new.title} ({span})")
    add("")
    add(
        f"**Overall severity:** {diff.severity.label.upper()} · "
        + " · ".join(f"{n} {k}" for k, n in diff.counts.items() if n)
    )
    add("")
    add("| | Before | After |")
    add("|---|---|---|")
    add(f"| Set ID | `{diff.old.set_id}` | `{diff.new.set_id}` |")
    add(f"| Version | {diff.old.version} | {diff.new.version} |")
    add(f"| Effective | {diff.old.effective_date or '—'} | {diff.new.effective_date or '—'} |")
    add(f"| Labeler | {diff.old.labeler or '—'} | {diff.new.labeler or '—'} |")
    add("")
    if summary is not None:
        add(f"## Summary — {summary.headline}")
        add("")
        source = (
            "rules-based"
            if summary.source == "deterministic"
            else f"AI ({summary.model}), {summary.verification_rate:.0%} of claims verified"
        )
        add(f"_{source}_")
        add("")
        add(summary.overview)
        add("")
        if summary.notice:
            add(f"> {summary.notice}")
            add("")
        for c in summary.claims:
            flag = "" if c.verified else " ⚠ unverified"
            add(f"- **[{c.significance}] {c.change_id}**{flag}: {c.summary}")
            for q in c.evidence_quotes:
                add(f"  > “{q}”")
        if summary.actions:
            add("")
            add("**Considerations for your labeling**")
            add("")
            for a in summary.actions:
                add(f"- {a}")
        add("")
    add("## Changes")
    add("")
    if not diff.changes:
        add("No differences in label text.")
    for ch in diff.changes:
        add(f"### [{ch.severity.label.upper()}] {ch.heading}")
        add("")
        add(f"_{ch.status} · {ch.family_label} · {ch.cid}_ — " + "; ".join(ch.reasons))
        add("")
        for h in ch.hunks:
            tag = " (editorial)" if h.editorial else ""
            add(f"- `{h.hid}`{tag}: {_md_segments(h.segments)}")
            if h.signals:
                add(f"  - signals: {'; '.join(h.signals)}")
        add("")
    add("---")
    add(
        f"Generated by LabelLedger {diff.engine_version} at {diff.generated_at}. Insertions in **bold**, deletions ~~struck~~. "
        "Decision support only — verify against official labeling."
    )
    return "\n".join(lines) + "\n"


def drift_to_markdown(report: DriftReport, *, min_severity: Severity = Severity.MODERATE) -> str:
    lines: list[str] = []
    add = lines.append
    add(f"# Label drift: {report.follower.title}")
    add("")
    add(
        f"**Parity:** {report.parity:.1%} · **Severity:** {report.severity.label.upper()}"
        + (f" · reference is {report.lag_days} days newer" if report.lag_days is not None else "")
    )
    add("")
    add("| | Reference | Follower |")
    add("|---|---|---|")
    add(f"| Set ID | `{report.reference.set_id}` | `{report.follower.set_id}` |")
    add(
        f"| Version / effective | v{report.reference.version} · {report.reference.effective_date or '—'} | "
        f"v{report.follower.version} · {report.follower.effective_date or '—'} |"
    )
    add(f"| Labeler | {report.reference.labeler or '—'} | {report.follower.labeler or '—'} |")
    add("")
    add("| Family | Parity | Missing | Changed | Extra |")
    add("|---|---|---|---|---|")
    for p in report.pools:
        add(f"| {p.label} | {p.parity:.0%} | {p.missing} | {p.changed} | {p.extra} |")
    add("")
    shown = [f for f in report.findings if f.severity >= min_severity]
    add(f"## Findings at {min_severity.label} or above ({len(shown)} of {len(report.findings)})")
    add("")
    for f in shown:
        add(
            f"- **[{f.severity.label.upper()}] {f.kind} · {f.pool_label}** (`{f.fid}`): "
            + "; ".join(f.reasons)
        )
        if f.kind == "changed":
            add(f"  - follower → reference: {_md_segments(f.segments)}")
        elif f.kind == "missing":
            add(f"  - reference: “{f.reference_text}”")
        else:
            add(f"  - follower: “{f.follower_text}”")
    add("")
    add("---")
    add(
        f"Generated by LabelLedger {report.engine_version} at {report.generated_at}. Decision support only."
    )
    return "\n".join(lines) + "\n"
