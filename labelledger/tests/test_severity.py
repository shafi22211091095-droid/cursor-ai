from __future__ import annotations

import pytest

from labelledger.severity import Severity, classify, hunk_signals, risk_terms_in


def test_signals():
    s = hunk_signals("take 5 mg daily", "take 2.5 mg daily")
    assert any(x.startswith("numbers changed: 5 → 2.5") for x in s)
    assert any("negation" in x for x in hunk_signals("use with food", "do not use with food"))
    assert any(
        x.startswith("risk terms added: fatal")
        for x in hunk_signals("", "fatal hemorrhage occurred")
    )
    assert any(x.startswith("risk terms removed") for x in hunk_signals("serious bleeding", ""))
    assert hunk_signals("same", "same") == []


def test_risk_terms_word_boundaries():
    assert "abuse" in risk_terms_in("risk of abuse and misuse")
    assert "dress" not in risk_terms_in("address the patient")


BASE = {
    "substantive_hunks": 1,
    "editorial_hunks": 0,
    "words_changed": 8,
    "signals": [],
    "title_changed": False,
    "title_editorial": True,
}


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"status": "added", "family": "boxed_warning", "tier": 1}, Severity.CRITICAL),
        ({"status": "removed", "family": "warnings_precautions", "tier": 1}, Severity.MAJOR),
        ({"status": "added", "family": "clinical_studies", "tier": 2}, Severity.MODERATE),
        ({"status": "added", "family": "how_supplied", "tier": 3}, Severity.MINOR),
        ({"status": "modified", "family": "contraindications", "tier": 1}, Severity.CRITICAL),
        (
            {
                "status": "modified",
                "family": "dosage_administration",
                "tier": 1,
                "signals": ["numbers changed: 5 → 4"],
            },
            Severity.CRITICAL,
        ),
        ({"status": "modified", "family": "dosage_administration", "tier": 1}, Severity.MAJOR),
        (
            {"status": "modified", "family": "adverse_reactions", "tier": 1, "words_changed": 2},
            Severity.MODERATE,
        ),
        ({"status": "modified", "family": "clinical_pharmacology", "tier": 2}, Severity.MODERATE),
        ({"status": "modified", "family": "description", "tier": 3}, Severity.MINOR),
        (
            {
                "status": "modified",
                "family": "description",
                "tier": 3,
                "substantive_hunks": 0,
                "editorial_hunks": 2,
            },
            Severity.EDITORIAL,
        ),
        (
            {
                "status": "modified",
                "family": "warnings_precautions",
                "tier": 1,
                "substantive_hunks": 0,
                "title_changed": True,
                "title_editorial": False,
            },
            Severity.MINOR,
        ),
        (
            {
                "status": "modified",
                "family": "warnings_precautions",
                "tier": 1,
                "substantive_hunks": 0,
            },
            Severity.NONE,
        ),
    ],
)
def test_classify(kw, expected):
    args = {**BASE, **kw}
    sev, reasons = classify(**args)
    assert sev is expected
    if sev is not Severity.NONE:
        assert reasons
