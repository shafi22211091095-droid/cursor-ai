"""Deterministic, explainable severity triage for label changes.

Severity answers one question for a labeling team: *how urgently must someone
read this change?* It is computed from rules, never from a model, so every
rating comes with the reasons that produced it and the same input always gives
the same output. AI summaries (``ai.py``) may describe a change, but they never
set or override its severity.
"""

from __future__ import annotations

import re
from collections import Counter
from enum import IntEnum

from . import text as T


class Severity(IntEnum):
    NONE = 0
    EDITORIAL = 1
    MINOR = 2
    MODERATE = 3
    MAJOR = 4
    CRITICAL = 5

    @property
    def label(self) -> str:
        return self.name.lower()


#: Words whose appearance or disappearance changes the meaning of an instruction.
NEGATIONS = frozenset(
    {"not", "no", "never", "avoid", "without", "contraindicated", "discontinue", "stop", "cannot"}
)

#: Risk vocabulary. A new occurrence in a changed line is a strong signal that a
#: safety statement was added; a removed occurrence that one was withdrawn.
RISK_TERMS = (
    "fatal",
    "death",
    "deaths",
    "died",
    "life-threatening",
    "life threatening",
    "serious",
    "severe",
    "hospitalization",
    "contraindicated",
    "suicidal",
    "suicide",
    "hepatotoxicity",
    "liver failure",
    "hepatic failure",
    "anaphylaxis",
    "anaphylactic",
    "angioedema",
    "stevens-johnson",
    "toxic epidermal necrolysis",
    "dress",
    "qt prolongation",
    "torsade",
    "arrhythmia",
    "cardiac arrest",
    "hemorrhage",
    "bleeding",
    "seizure",
    "respiratory depression",
    "overdose",
    "abuse",
    "addiction",
    "misuse",
    "teratogenic",
    "embryo-fetal",
    "fetal harm",
    "birth defects",
    "malignancy",
    "cancer",
    "lactic acidosis",
    "agranulocytosis",
    "neutropenia",
    "pancreatitis",
    "rhabdomyolysis",
    "renal failure",
    "kidney injury",
    "stroke",
    "myocardial infarction",
    "necrosis",
    "gangrene",
    "calciphylaxis",
    "boxed warning",
)

DOSE_UNITS = frozenset(
    {
        "mg",
        "mcg",
        "µg",
        "g",
        "kg",
        "ml",
        "l",
        "units",
        "unit",
        "iu",
        "meq",
        "mmol",
        "daily",
        "once",
        "twice",
        "times",
        "hours",
        "hour",
        "hourly",
        "days",
        "weeks",
        "maximum",
        "minimum",
        "titrate",
        "dose",
        "doses",
        "dosage",
        "inr",
    }
)

_WORD = re.compile(r"[a-zµ]+")


def risk_terms_in(text: str) -> Counter[str]:
    folded = T.fold(text).lower()
    found: Counter[str] = Counter()
    for term in RISK_TERMS:
        pattern = r"(?<![a-z])" + re.escape(term) + r"(?![a-z])"
        n = len(re.findall(pattern, folded))
        if n:
            found[term] = n
    return found


def words(text: str) -> set[str]:
    return set(_WORD.findall(T.fold(text).lower()))


def hunk_signals(old: str, new: str) -> list[str]:
    """Signals for one changed line. ``old`` or ``new`` may be empty (pure add/remove)."""
    signals: list[str] = []
    old_numbers, new_numbers = Counter(T.numbers(old)), Counter(T.numbers(new))
    if old and new and old_numbers != new_numbers:
        gone = sorted((old_numbers - new_numbers).elements())
        came = sorted((new_numbers - old_numbers).elements())
        signals.append(f"numbers changed: {', '.join(gone) or '∅'} → {', '.join(came) or '∅'}")
    old_words, new_words = words(old), words(new)
    added, removed = new_words - old_words, old_words - new_words
    neg = sorted((added | removed) & NEGATIONS)
    if neg and old and new:
        signals.append(f"negation/instruction words changed: {', '.join(neg)}")
    if (added | removed) & DOSE_UNITS and old and new:
        signals.append("dosing vocabulary changed")
    risk_old, risk_new = risk_terms_in(old), risk_terms_in(new)
    risk_added = sorted((risk_new - risk_old).keys())
    risk_removed = sorted((risk_old - risk_new).keys())
    if risk_added:
        signals.append(f"risk terms added: {', '.join(risk_added)}")
    if risk_removed:
        signals.append(f"risk terms removed: {', '.join(risk_removed)}")
    return signals


def classify(
    *,
    status: str,
    family: str,
    tier: int,
    substantive_hunks: int,
    editorial_hunks: int,
    words_changed: int,
    signals: list[str],
    title_changed: bool,
    title_editorial: bool,
) -> tuple[Severity, list[str]]:
    """Rate one section-level change and say why."""
    reasons: list[str] = []
    has_numbers = any(s.startswith("numbers changed") for s in signals)
    has_risk_added = any(s.startswith("risk terms added") for s in signals)
    has_risk_removed = any(s.startswith("risk terms removed") for s in signals)
    has_negation = any(s.startswith("negation") for s in signals)

    if status in ("added", "removed"):
        verb = "added" if status == "added" else "removed"
        if family in ("boxed_warning", "contraindications"):
            reasons.append(f"Entire {family.replace('_', ' ')} section {verb}")
            return Severity.CRITICAL, reasons
        if tier == 1:
            reasons.append(f"Safety-critical section {verb}")
            return Severity.MAJOR, reasons
        if tier == 2:
            reasons.append(f"Clinical section {verb}")
            return Severity.MODERATE, reasons
        reasons.append(f"Administrative section {verb}")
        return Severity.MINOR, reasons

    if substantive_hunks == 0:
        if title_changed and not title_editorial:
            reasons.append("Section heading changed")
            return Severity.MINOR, reasons
        if editorial_hunks or title_changed:
            reasons.append("Formatting, punctuation, case or cross-reference changes only")
            return Severity.EDITORIAL, reasons
        return Severity.NONE, reasons

    reasons.append(f"{substantive_hunks} substantive change(s), {words_changed} word(s) affected")
    if family == "boxed_warning":
        reasons.append("Boxed warning text changed")
        return Severity.CRITICAL, reasons
    if family == "contraindications":
        reasons.append("Contraindications changed")
        return Severity.CRITICAL, reasons
    if family == "dosage_administration" and has_numbers:
        reasons.append("Numeric dosing values changed")
        return Severity.CRITICAL, reasons

    if tier == 1:
        if has_risk_added:
            reasons.append("New risk language in a safety-critical section")
        if has_risk_removed:
            reasons.append("Risk language removed from a safety-critical section")
        if has_negation:
            reasons.append("Instruction or negation words changed")
        if has_numbers:
            reasons.append("Numeric values changed")
        if words_changed <= 3 and not (
            has_risk_added or has_risk_removed or has_negation or has_numbers
        ):
            reasons.append("Small wording change")
            return Severity.MODERATE, reasons
        return Severity.MAJOR, reasons
    if tier == 2:
        if has_risk_added or has_numbers:
            reasons.append("Risk language or numeric values changed in a clinical section")
        return Severity.MODERATE, reasons
    return Severity.MINOR, reasons
