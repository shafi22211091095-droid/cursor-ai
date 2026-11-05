"""Grounded impact summaries for label diffs.

Two summarisers share one output type, :class:`SummaryResult`:

* :func:`deterministic_summary` — always available, rule-based, quotes only
  text that is in the diff. This is the default and what tests exercise.
* :func:`summarize` with AI enabled — Claude writes a plain-language summary as
  structured output. **Every claim is then verified against the diff**: each
  must cite change and hunk IDs that exist, and every evidence quote must occur
  verbatim (after whitespace/typography folding) in the cited hunks. Claims
  that fail are kept but marked unverified, so a reviewer can see what the
  model said and that it could not be checked. AI never sets severity; the
  rules-based severity in ``severity.py`` is always the one shown.

Failure is never silent and never fatal: no credentials, a refusal, a
truncated response or an API error all fall back to the deterministic summary
with a notice explaining why.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import text as T
from .diff import LabelDiff, SectionChange
from .severity import Severity

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PAYLOAD_BUDGET_CHARS = 120_000
AIMode = Literal["auto", "on", "off"]

SYSTEM_PROMPT = """You are a regulatory labeling analyst helping generic-drug labeling teams \
understand changes between two versions of a US prescription drug label (FDA Structured Product \
Labeling). You receive a machine-computed redline as JSON: sections that changed, each with hunks \
of old and new text. The redline is the ground truth; you explain it.

Write for a regulatory-affairs professional who must decide whether and how their own labels \
must change. Be precise and brief. Do not speculate beyond the redline.

Rules:
1. Every claim must cite the change_id and the hunk_ids it describes.
2. Every claim must include at least one evidence quote copied character-for-character from the \
"old" or "new" text of a cited hunk. Quote the shortest span that proves the claim (normally 5-30 \
words). Never paraphrase inside a quote, never join text from different hunks into one quote.
3. Describe what changed in clinical terms (new warning, dose change, new contraindication, \
removed statement, wording clarification). Say plainly when a change is editorial.
4. "significance" is your reading of clinical importance; the rules-based severity is shown to \
users separately and is not overridden by yours.
5. actions_for_generic_labeling: concrete next steps a generic labeling team would consider, \
phrased as considerations, not legal or regulatory advice. Mention when an Indications change may \
relate to patent or exclusivity carve-outs rather than assuming it must be copied.
6. open_questions: anything the redline cannot settle (for example, whether a change applies to \
all strengths).
7. Cover every change whose rules_severity is critical or major. Group editorial changes into a \
single short claim or omit them."""


class ChangeClaim(BaseModel):
    change_id: str = Field(description="ID of the section change, e.g. 'c3'")
    hunk_ids: list[str] = Field(description="IDs of the hunks this claim describes, e.g. ['c3.h1']")
    summary: str = Field(description="One or two sentences describing the change")
    evidence_quotes: list[str] = Field(
        description="Verbatim spans from the cited hunks' old/new text"
    )
    significance: Literal["critical", "major", "moderate", "minor", "editorial"]


class ImpactSummary(BaseModel):
    headline: str = Field(description="One line, at most 20 words")
    overview: str = Field(description="Two to four sentences")
    changes: list[ChangeClaim]
    actions_for_generic_labeling: list[str]
    open_questions: list[str]


@dataclass
class VerifiedClaim:
    change_id: str
    hunk_ids: list[str]
    summary: str
    evidence_quotes: list[str]
    significance: str
    verified: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class SummaryResult:
    source: Literal["ai", "deterministic"]
    model: str | None
    headline: str
    overview: str
    claims: list[VerifiedClaim]
    actions: list[str]
    open_questions: list[str]
    notice: str | None = None
    usage: dict[str, int] | None = None

    @property
    def verification_rate(self) -> float:
        if not self.claims:
            return 1.0
        return sum(c.verified for c in self.claims) / len(self.claims)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SummaryResult:
        """Rebuild a stored summary (the inverse of :meth:`to_dict`)."""
        return cls(
            source="ai" if data.get("source") == "ai" else "deterministic",
            model=data.get("model"),
            headline=str(data.get("headline", "")),
            overview=str(data.get("overview", "")),
            claims=[
                VerifiedClaim(
                    change_id=str(c.get("change_id", "")),
                    hunk_ids=list(c.get("hunk_ids", [])),
                    summary=str(c.get("summary", "")),
                    evidence_quotes=list(c.get("evidence_quotes", [])),
                    significance=str(c.get("significance", "")),
                    verified=bool(c.get("verified", False)),
                    problems=list(c.get("problems", [])),
                )
                for c in data.get("claims", [])
            ],
            actions=list(data.get("actions", [])),
            open_questions=list(data.get("open_questions", [])),
            notice=data.get("notice"),
            usage=data.get("usage"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model": self.model,
            "headline": self.headline,
            "overview": self.overview,
            "verification_rate": round(self.verification_rate, 4),
            "notice": self.notice,
            "usage": self.usage,
            "claims": [
                {
                    "change_id": c.change_id,
                    "hunk_ids": c.hunk_ids,
                    "summary": c.summary,
                    "evidence_quotes": c.evidence_quotes,
                    "significance": c.significance,
                    "verified": c.verified,
                    "problems": c.problems,
                }
                for c in self.claims
            ],
            "actions": self.actions,
            "open_questions": self.open_questions,
        }


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def build_payload(diff: LabelDiff, budget: int = PAYLOAD_BUDGET_CHARS) -> str:
    """Serialise the redline for the model, most severe changes first, within a budget.

    Nothing is dropped silently: changes that do not fit are listed by ID under
    ``omitted`` so the model (and the reader of the summary) knows they exist.
    """
    header = {
        "label": {
            "title": diff.new.title,
            "set_id": diff.new.set_id,
            "labeler": diff.new.labeler,
            "application_numbers": list(diff.new.application_numbers),
            "old_version": diff.old.version,
            "new_version": diff.new.version,
            "old_effective_date": diff.old.effective_date.isoformat()
            if diff.old.effective_date
            else None,
            "new_effective_date": diff.new.effective_date.isoformat()
            if diff.new.effective_date
            else None,
            "same_label": diff.same_set,
        },
        "recent_major_changes_listed_in_new_label": diff.recent_major_changes,
    }
    ordered = sorted(diff.changes, key=lambda c: (-int(c.severity), c.cid))
    included: list[dict[str, Any]] = []
    omitted: list[str] = []
    used = len(json.dumps(header))
    for c in ordered:
        if c.severity <= Severity.EDITORIAL:
            entry: dict[str, Any] = {
                "change_id": c.cid,
                "section": c.heading,
                "status": c.status,
                "rules_severity": c.severity.label,
                "note": "editorial-only; hunks not included",
            }
        else:
            entry = {
                "change_id": c.cid,
                "section": c.heading,
                "family": c.family_label,
                "status": c.status,
                "rules_severity": c.severity.label,
                "reasons": c.reasons,
                "hunks": [
                    {
                        "hunk_id": h.hid,
                        "kind": h.kind,
                        "editorial": h.editorial,
                        "old": h.old,
                        "new": h.new,
                        "inserted_words": h.inserted,
                        "deleted_words": h.deleted,
                    }
                    for h in c.hunks
                ],
            }
        size = len(json.dumps(entry, ensure_ascii=False))
        if used + size > budget:
            omitted.append(c.cid)
            continue
        included.append(entry)
        used += size
    payload = {**header, "changes": included}
    if omitted:
        payload["omitted"] = {
            "change_ids": omitted,
            "reason": "payload size budget; see full report",
        }
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    return T.fold(s).lower()


def verify_claims(claims: list[ChangeClaim], diff: LabelDiff) -> list[VerifiedClaim]:
    by_id: dict[str, SectionChange] = {c.cid: c for c in diff.changes}
    out: list[VerifiedClaim] = []
    for claim in claims:
        problems: list[str] = []
        change = by_id.get(claim.change_id)
        evidence = ""
        if change is None:
            problems.append(f"unknown change_id {claim.change_id!r}")
        else:
            hunks = {h.hid: h for h in change.hunks}
            cited = [hunks[h] for h in claim.hunk_ids if h in hunks]
            unknown = [h for h in claim.hunk_ids if h not in hunks]
            if unknown:
                problems.append(f"hunk ids not in this change: {', '.join(unknown)}")
            if not claim.hunk_ids:
                problems.append("no hunk ids cited")
            evidence = _norm("\n".join(h.evidence_text() for h in cited))
            if not claim.hunk_ids and change.hunks:
                evidence = _norm("\n".join(h.evidence_text() for h in change.hunks))
        if not claim.evidence_quotes:
            problems.append("no evidence quotes")
        for quote in claim.evidence_quotes:
            if not quote.strip():
                problems.append("empty quote")
            elif _norm(quote) not in evidence:
                problems.append(f"quote not found in cited hunks: {quote[:60]!r}")
        if change is not None and claim.significance != change.severity.label:
            problems.append(
                f"model rated {claim.significance}; rules-based severity is {change.severity.label}"
            )
        hard = [p for p in problems if not p.startswith("model rated")]
        out.append(
            VerifiedClaim(
                change_id=claim.change_id,
                hunk_ids=list(claim.hunk_ids),
                summary=claim.summary,
                evidence_quotes=list(claim.evidence_quotes),
                significance=claim.significance,
                verified=not hard,
                problems=problems,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Deterministic summariser
# ---------------------------------------------------------------------------


def _first_words(text: str, n: int = 30) -> str:
    words = text.split()
    return " ".join(words[:n])


_ACTIONS: dict[str, str] = {
    "boxed_warning": "Treat the Boxed Warning change as top priority: compare it word for word with your label's Boxed Warning.",
    "contraindications": "Review the Contraindications change against your label; contraindications must match the reference.",
    "dosage_administration": "Verify every dosing value and instruction in your Dosage and Administration section against the revised reference.",
    "warnings_precautions": "Assess whether your Warnings and Precautions section needs the new or revised statements.",
    "adverse_reactions": "Check your Adverse Reactions section against the revised list.",
    "drug_interactions": "Check your Drug Interactions section and any interaction tables.",
    "specific_populations": "Review Use in Specific Populations (pregnancy, lactation, pediatrics, geriatrics, organ impairment).",
    "indications": "Review the Indications change; confirm whether it is subject to a patent or exclusivity carve-out before copying it.",
    "patient_labeling": "Update patient-facing labeling (Medication Guide / Patient Information) to stay consistent with the professional label.",
    "patient_counseling": "Update Patient Counseling Information to reflect the revised professional label.",
}


def deterministic_summary(diff: LabelDiff, notice: str | None = None) -> SummaryResult:
    material = [c for c in diff.changes if c.severity >= Severity.MINOR]
    editorial = [c for c in diff.changes if c.severity is Severity.EDITORIAL]
    sev = diff.severity
    if material:
        headline = f"{len(material)} material change(s); highest severity {sev.label.upper()}"
    elif editorial:
        headline = "Editorial changes only"
    else:
        headline = "No changes detected"
    span = (
        f"version {diff.old.version} → {diff.new.version}"
        if diff.same_set
        else "two different labels"
    )
    top = ", ".join(sorted({c.family_label for c in material})[:6]) or "none"
    overview = (
        f"Comparison of {span} of {diff.new.title}. "
        f"Sections with material changes: {top}. "
        f"Editorial-only sections: {len(editorial)}."
    )
    claims: list[VerifiedClaim] = []
    for c in sorted(material, key=lambda c: (-int(c.severity), c.cid)):
        subst = c.substantive or c.hunks
        quotes: list[str] = []
        parts: list[str] = []
        for h in subst[:3]:
            if h.kind == "added" and h.new:
                quotes.append(_first_words(h.new))
                parts.append("text added")
            elif h.kind == "removed" and h.old:
                quotes.append(_first_words(h.old))
                parts.append("text removed")
            elif h.new:
                quotes.append(_first_words(h.new))
                parts.append("text revised")
        verb = {"added": "Section added", "removed": "Section removed"}.get(
            c.status, "Section revised"
        )
        detail = "; ".join(dict.fromkeys(parts)) or "heading changed"
        summary = f"{verb} ({detail}). " + " ".join(r.rstrip(".") + "." for r in c.reasons[-2:])
        claims.append(
            VerifiedClaim(
                change_id=c.cid,
                hunk_ids=[h.hid for h in subst[:3]],
                summary=summary.strip(),
                evidence_quotes=quotes,
                significance=c.severity.label,
                verified=True,
            )
        )
    actions = list(dict.fromkeys(_ACTIONS[c.family] for c in material if c.family in _ACTIONS))
    if material:
        actions.append(
            "Record the assessment and, where your label must change, prepare the revised labeling "
            "and side-by-side comparison against the reference."
        )
    questions = []
    if any(c.family == "indications" for c in material):
        questions.append(
            "Does the Indications change fall under a patent or exclusivity carve-out?"
        )
    if not diff.same_set:
        questions.append(
            "These are different labels (set IDs differ); is a drift report more appropriate?"
        )
    return SummaryResult(
        source="deterministic",
        model=None,
        headline=headline,
        overview=overview,
        claims=claims,
        actions=actions,
        open_questions=questions,
        notice=notice,
    )


# ---------------------------------------------------------------------------
# AI summariser
# ---------------------------------------------------------------------------


def _env_has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def resolve_mode(mode: AIMode | None = None) -> AIMode:
    raw = (mode or os.environ.get("LABELLEDGER_AI", "auto")).lower()
    return raw if raw in ("auto", "on", "off") else "auto"  # type: ignore[return-value]


def _usage(response: Any) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    out: dict[str, int] = {}
    for name in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = getattr(usage, name, None)
        if isinstance(value, int):
            out[name] = value
    return out


def summarize(
    diff: LabelDiff,
    *,
    mode: AIMode | None = None,
    client: Any = None,
    model: str | None = None,
) -> SummaryResult:
    """Summarise a diff, with AI when enabled and available, else deterministically."""
    resolved = resolve_mode(mode)
    if resolved == "off":
        return deterministic_summary(diff)
    if not [c for c in diff.changes if c.severity >= Severity.MINOR]:
        return deterministic_summary(diff)
    if client is None:
        if resolved == "auto" and not _env_has_credentials():
            return deterministic_summary(diff)
        try:
            import anthropic
        except ImportError:
            return deterministic_summary(diff, "AI summary unavailable: install labelledger[ai].")
        try:
            client = anthropic.Anthropic(max_retries=2, timeout=180.0)
        except anthropic.AnthropicError as exc:
            return deterministic_summary(diff, f"AI summary unavailable: {exc}")
    chosen = model or os.environ.get("LABELLEDGER_AI_MODEL", DEFAULT_MODEL)
    return _call_model(diff, client, chosen)


def _call_model(diff: LabelDiff, client: Any, model: str) -> SummaryResult:
    try:
        import anthropic
    except ImportError:
        return deterministic_summary(diff, "AI summary unavailable: install labelledger[ai].")
    payload = build_payload(diff)
    reason: str | None = None
    try:
        response = client.beta.messages.parse(
            model=model,
            max_tokens=16000,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": payload}],
            output_format=ImpactSummary,
            output_config={"effort": "medium"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    # Most specific first, so retryable and non-retryable failures stay distinct.
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        reason = "credentials were rejected"
    except anthropic.RateLimitError:
        reason = "rate limited; try again shortly"
    except anthropic.BadRequestError as exc:
        reason = f"request rejected ({exc.message})"
    except anthropic.APIStatusError as exc:
        reason = f"API error {exc.status_code}"
    except anthropic.APIConnectionError:
        reason = "could not reach the API"
    except anthropic.AnthropicError as exc:
        reason = str(exc) or exc.__class__.__name__
    if reason is not None:
        log.warning("AI summary failed: %s", reason)
        return deterministic_summary(diff, f"AI summary unavailable: {reason}.")

    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None)
        return deterministic_summary(
            diff, f"AI summary declined by the model (category: {category})."
        )
    if stop == "max_tokens":
        return deterministic_summary(
            diff, "AI summary was truncated; showing the deterministic summary."
        )
    parsed = getattr(response, "parsed_output", None)
    if not isinstance(parsed, ImpactSummary):
        return deterministic_summary(
            diff, "AI summary could not be parsed; showing the deterministic summary."
        )
    claims = verify_claims(parsed.changes, diff)
    result = SummaryResult(
        source="ai",
        model=getattr(response, "model", model),
        headline=parsed.headline,
        overview=parsed.overview,
        claims=claims,
        actions=list(parsed.actions_for_generic_labeling),
        open_questions=list(parsed.open_questions),
        usage=_usage(response),
    )
    if result.verification_rate < 1.0:
        failed = sum(not c.verified for c in claims)
        result.notice = (
            f"{failed} of {len(claims)} AI claim(s) could not be verified against the redline "
            "and are marked unverified."
        )
    return result
