"""Label drift: is a follower label (e.g. a generic) still in sync with its reference?

Version diffs compare two versions of *one* label, whose structure is stable.
Drift compares two *different* labels — a reference listed drug and a generic,
or the current and an older reference — whose structure often differs: one may
be in PLR format with numbered subsections, the other flat or in the old
WARNINGS/PRECAUTIONS layout. So drift works at the level of statements:

1. Pool each label's sentences by clinical family (sections.py), so the same
   content filed under different codes still meets.
2. For every reference sentence, find the follower sentence that reproduces
   the most of it, in order (token coverage), using a fast fuzzy pre-filter.
3. Classify: *present* (≥ PRESENT coverage, same numbers), *changed* (partial
   coverage or different numbers), *missing* (below CHANGED coverage). Follower
   sentences that no reference sentence explains are *extra* — often text the
   reference has since removed.
4. Triage each finding by section tier, numbers and risk vocabulary.

Product-specific families (description, how supplied, packaging, product data)
are skipped: they legitimately differ. Indications may differ because of
patent or exclusivity carve-outs; the report says so rather than guessing.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import Any, Literal

from rapidfuzz import fuzz, process

from . import __version__
from . import text as T
from .diff import DocRef, Segment, word_segments
from .sections import drift_pool, is_product_specific, label_of, tier_of
from .severity import Severity, hunk_signals, risk_terms_in
from .spl import LabelDocument

FindingKind = Literal["missing", "changed", "extra"]

PRESENT = 0.92
CHANGED = 0.55
MIN_TOKENS = 4
CANDIDATES = 4
_RMC_DATE = re.compile(r"(\d{1,2})/(\d{4})\s*$")


@dataclass
class _Sentence:
    text: str
    key: str
    tokens: list[str]
    section: str


@dataclass
class DriftFinding:
    fid: str
    kind: FindingKind
    pool: str
    tier: int
    reference_text: str | None
    follower_text: str | None
    coverage: float
    severity: Severity
    reasons: list[str]
    signals: list[str] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    reference_section: str | None = None
    follower_section: str | None = None

    @property
    def pool_label(self) -> str:
        return label_of(self.pool)


@dataclass
class PoolSummary:
    pool: str
    tier: int
    reference_sentences: int
    follower_sentences: int
    present: int
    changed: int
    missing: int
    extra: int
    parity: float

    @property
    def label(self) -> str:
        return label_of(self.pool)


@dataclass
class DriftReport:
    reference: DocRef
    follower: DocRef
    pools: list[PoolSummary]
    findings: list[DriftFinding]
    reference_changes_after_follower: list[str]
    skipped_pools: list[str]
    generated_at: str
    engine_version: str = __version__

    @property
    def parity(self) -> float:
        weights = {1: 3.0, 2: 1.0, 3: 0.5}
        num = sum(p.parity * weights.get(p.tier, 1.0) * p.reference_sentences for p in self.pools)
        den = sum(weights.get(p.tier, 1.0) * p.reference_sentences for p in self.pools)
        return num / den if den else 1.0

    @property
    def severity(self) -> Severity:
        return max((f.severity for f in self.findings), default=Severity.NONE)

    @property
    def lag_days(self) -> int | None:
        r, f = self.reference.effective_date, self.follower.effective_date
        if r is None or f is None:
            return None
        return (r - f).days

    @property
    def counts(self) -> dict[str, int]:
        out = {s.label: 0 for s in Severity if s is not Severity.NONE}
        for finding in self.findings:
            out[finding.severity.label] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": {"name": "labelledger", "version": self.engine_version},
            "generated_at": self.generated_at,
            "reference": self.reference.to_dict(),
            "follower": self.follower.to_dict(),
            "parity": round(self.parity, 4),
            "severity": self.severity.label,
            "counts": self.counts,
            "lag_days": self.lag_days,
            "reference_changes_after_follower": self.reference_changes_after_follower,
            "skipped_pools": self.skipped_pools,
            "pools": [
                {
                    "pool": p.pool,
                    "label": p.label,
                    "tier": p.tier,
                    "reference_sentences": p.reference_sentences,
                    "follower_sentences": p.follower_sentences,
                    "present": p.present,
                    "changed": p.changed,
                    "missing": p.missing,
                    "extra": p.extra,
                    "parity": round(p.parity, 4),
                }
                for p in self.pools
            ],
            "findings": [
                {
                    "id": f.fid,
                    "kind": f.kind,
                    "pool": f.pool,
                    "tier": f.tier,
                    "severity": f.severity.label,
                    "reasons": f.reasons,
                    "coverage": round(f.coverage, 4),
                    "reference_text": f.reference_text,
                    "follower_text": f.follower_text,
                    "reference_section": f.reference_section,
                    "follower_section": f.follower_section,
                    "signals": f.signals,
                    "segments": [{"op": s.op, "text": s.text} for s in f.segments],
                }
                for f in self.findings
            ],
        }


def _pools(doc: LabelDocument) -> tuple[dict[str, list[_Sentence]], set[str]]:
    pools: dict[str, list[_Sentence]] = {}
    skipped: set[str] = set()
    for root in doc.sections:
        for section in root.walk():
            if is_product_specific(section.family):
                skipped.add(section.family)
                continue
            pool = drift_pool(section.family)
            heading = section.heading if section is root else f"{root.heading} › {section.heading}"
            for line in section.lines:
                for sentence in T.split_sentences(line):
                    key = T.compare_key(sentence)
                    tokens = key.split()
                    if len(tokens) < MIN_TOKENS:
                        continue
                    pools.setdefault(pool, []).append(_Sentence(sentence, key, tokens, heading))
    return pools, skipped


def _coverage(a: list[str], b: list[str]) -> float:
    """Fraction of ``a``'s tokens reproduced, in order, in ``b``."""
    if not a:
        return 1.0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return sum(block.size for block in sm.get_matching_blocks()) / len(a)


def _best_match(
    s: _Sentence, others: list[_Sentence], keys: list[str], exact: dict[str, int]
) -> tuple[_Sentence | None, float, float]:
    """Best counterpart of ``s`` among ``others``: (sentence, coverage, similarity).

    An identical comparison key wins outright. Otherwise the fuzzy pre-filter
    proposes candidates and the one reproducing most of ``s`` in order wins,
    preferring equal numbers, then overall similarity.
    """
    if not others:
        return None, 0.0, 0.0
    if s.key in exact:
        return others[exact[s.key]], 1.0, 1.0
    hits = process.extract(s.key, keys, scorer=fuzz.token_set_ratio, limit=CANDIDATES)
    best: tuple[float, bool, float] | None = None
    best_sentence: _Sentence | None = None
    for _, _, idx in hits:
        cand = others[idx]
        cov = _coverage(s.tokens, cand.tokens)
        ratio = fuzz.ratio(s.key, cand.key) / 100.0
        score = (cov, not _numbers_lost(s.text, cand.text), ratio)
        if best is None or score > best:
            best, best_sentence = score, cand
    if best is None:
        return None, 0.0, 0.0
    return best_sentence, best[0], best[2]


def _is_counterpart(s: _Sentence, coverage: float, ratio: float) -> bool:
    """Is the best match close enough to call it the same statement, reworded?

    Either nearly all of the statement survives inside the match (it was
    merged into a longer sentence), or most of it survives and the two read
    alike overall. Short statements need more of their words to survive.
    """
    if coverage >= 0.85:
        return True
    needed = 0.75 if len(s.tokens) <= 10 else CHANGED
    return coverage >= needed and ratio >= 0.6


def _numbers_lost(ref: str, fol: str) -> bool:
    """True when a number stated in the reference does not appear in the follower."""
    return bool(Counter(T.numbers(ref)) - Counter(T.numbers(fol)))


def _rate(
    kind: FindingKind, pool: str, tier: int, ref: str, fol: str, coverage: float
) -> tuple[Severity, list[str]]:
    """Triage a drift finding. Conservative: CRITICAL only for boxed warnings and contraindications."""
    reasons: list[str] = []
    numbers_differ = _numbers_lost(ref, fol) if (ref and fol) else False
    ref_risk = set(risk_terms_in(ref)) if ref else set()
    fol_risk = set(risk_terms_in(fol)) if fol else set()
    critical_pool = pool in ("boxed_warning", "contraindications")
    if kind == "missing":
        reasons.append("Reference statement has no counterpart in the follower label")
        if ref_risk:
            reasons.append(f"Statement carries risk language: {', '.join(sorted(ref_risk))}")
        if critical_pool:
            return Severity.CRITICAL, reasons
        if tier == 1:
            return (Severity.MAJOR if ref_risk or T.numbers(ref) else Severity.MODERATE), reasons
        return (Severity.MODERATE if ref_risk else Severity.MINOR), reasons
    if kind == "changed":
        reasons.append(
            f"Follower wording differs from the reference ({coverage:.0%} of the statement reproduced)"
        )
        if numbers_differ:
            reasons.append("Numeric values differ")
        lost = ref_risk - fol_risk
        if lost:
            reasons.append(
                f"Risk language in the reference is absent here: {', '.join(sorted(lost))}"
            )
        if critical_pool:
            if numbers_differ or lost:
                return Severity.CRITICAL, reasons
            return (Severity.MAJOR if coverage < 0.8 else Severity.MODERATE), reasons
        if tier == 1 and (numbers_differ or lost):
            return Severity.MAJOR, reasons
        if tier == 1:
            return (Severity.MINOR if coverage >= 0.8 else Severity.MODERATE), reasons
        return Severity.MINOR, reasons
    reasons.append(
        "Follower statement not found in the reference (possibly removed from the reference)"
    )
    if fol_risk:
        reasons.append(f"Statement carries risk language: {', '.join(sorted(fol_risk))}")
    if tier == 1 and fol_risk:
        return Severity.MODERATE, reasons
    return Severity.MINOR, reasons


def _rmc_after(reference: LabelDocument, follower_date: date | None) -> list[str]:
    if follower_date is None:
        return []
    out: list[str] = []
    for line in reference.recent_major_changes():
        m = _RMC_DATE.search(line)
        if not m:
            continue
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and (year, month) > (follower_date.year, follower_date.month):
            out.append(line)
    return out


_POOL_ORDER = [
    "boxed_warning",
    "indications",
    "dosage_administration",
    "contraindications",
    "warnings_precautions",
    "adverse_reactions",
    "drug_interactions",
    "specific_populations",
    "abuse_dependence",
    "overdosage",
    "clinical_pharmacology",
    "nonclinical_toxicology",
    "clinical_studies",
    "patient_information",
    "other",
]


def compare_labels(
    reference: LabelDocument, follower: LabelDocument, *, now: datetime | None = None
) -> DriftReport:
    """Measure how far ``follower`` has drifted from ``reference``."""
    ref_pools, ref_skipped = _pools(reference)
    fol_pools, fol_skipped = _pools(follower)
    order = {p: i for i, p in enumerate(_POOL_ORDER)}
    all_pools = sorted(set(ref_pools) | set(fol_pools), key=lambda p: (order.get(p, 99), p))

    findings: list[DriftFinding] = []
    summaries: list[PoolSummary] = []
    for pool in all_pools:
        refs, fols = ref_pools.get(pool, []), fol_pools.get(pool, [])
        tier = tier_of(pool) if pool != "patient_information" else 2
        ref_keys, fol_keys = [s.key for s in refs], [s.key for s in fols]
        ref_exact = {k: i for i, k in reversed(list(enumerate(ref_keys)))}
        fol_exact = {k: i for i, k in reversed(list(enumerate(fol_keys)))}
        present = changed = missing = 0
        covered_tokens = 0.0
        for s in refs:
            match, cov, ratio = _best_match(s, fols, fol_keys, fol_exact)
            numbers_same = match is not None and not _numbers_lost(s.text, match.text)
            covered_tokens += cov * len(s.tokens)
            if match is not None and cov >= PRESENT and numbers_same:
                present += 1
                continue
            counterpart = match is not None and _is_counterpart(s, cov, ratio)
            kind: FindingKind = "changed" if counterpart else "missing"
            fol_text = match.text if (match is not None and counterpart) else ""
            signals = (
                hunk_signals(fol_text, s.text) if kind == "changed" else hunk_signals("", s.text)
            )
            severity, reasons = _rate(kind, pool, tier, s.text, fol_text, cov)
            if kind == "changed":
                changed += 1
            else:
                missing += 1
            findings.append(
                DriftFinding(
                    fid="",
                    kind=kind,
                    pool=pool,
                    tier=tier,
                    reference_text=s.text,
                    follower_text=fol_text or None,
                    coverage=cov,
                    severity=severity,
                    reasons=reasons,
                    signals=signals,
                    segments=word_segments(fol_text, s.text) if kind == "changed" else [],
                    reference_section=s.section,
                    follower_section=match.section if (match is not None and counterpart) else None,
                )
            )
        extra = 0
        for f in fols:
            match, cov, ratio = _best_match(f, refs, ref_keys, ref_exact)
            if match is not None and _is_counterpart(f, cov, ratio):
                continue
            extra += 1
            severity, reasons = _rate("extra", pool, tier, "", f.text, cov)
            findings.append(
                DriftFinding(
                    fid="",
                    kind="extra",
                    pool=pool,
                    tier=tier,
                    reference_text=None,
                    follower_text=f.text,
                    coverage=cov,
                    severity=severity,
                    reasons=reasons,
                    follower_section=f.section,
                )
            )
        ref_tokens = sum(len(s.tokens) for s in refs)
        summaries.append(
            PoolSummary(
                pool=pool,
                tier=tier,
                reference_sentences=len(refs),
                follower_sentences=len(fols),
                present=present,
                changed=changed,
                missing=missing,
                extra=extra,
                parity=(covered_tokens / ref_tokens) if ref_tokens else (1.0 if not fols else 0.0),
            )
        )

    kind_rank = {"missing": 0, "changed": 1, "extra": 2}
    findings.sort(key=lambda f: (-int(f.severity), order.get(f.pool, 99), kind_rank[f.kind]))
    for i, finding in enumerate(findings, start=1):
        finding.fid = f"d{i}"
    stamp = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    return DriftReport(
        reference=DocRef.of(reference),
        follower=DocRef.of(follower),
        pools=summaries,
        findings=findings,
        reference_changes_after_follower=_rmc_after(reference, follower.effective_date),
        skipped_pools=sorted(ref_skipped | fol_skipped),
        generated_at=stamp,
    )
