from __future__ import annotations

import json
from datetime import date

from labelledger.drift import _rmc_after, compare_labels
from labelledger.severity import Severity

from .conftest import SYNTHETIC_EDITS


def test_label_compared_with_itself_has_full_parity(warfarin_v20):
    r = compare_labels(warfarin_v20, warfarin_v20)
    assert r.findings == [] and r.parity == 1.0 and r.lag_days == 0
    assert r.severity is Severity.NONE


def test_synthetic_reference_update_is_detected(warfarin_v20, warfarin_v21):
    r = compare_labels(warfarin_v21, warfarin_v20)
    kinds = sorted((f.kind, f.pool) for f in r.findings)
    assert kinds == [
        ("changed", "dosage_administration"),
        ("missing", "warnings_precautions"),
        ("missing", "warnings_precautions"),
        ("missing", "warnings_precautions"),
    ]
    missing = {f.reference_text for f in r.findings if f.kind == "missing"}
    assert SYNTHETIC_EDITS["aki_sentence"] in missing
    changed = next(f for f in r.findings if f.kind == "changed")
    assert {s.text.strip() for s in changed.segments if s.op == "ins"} == {"4"}
    assert all(f.severity is Severity.MAJOR for f in r.findings)
    assert 0.99 < r.parity < 1.0


def test_real_outdated_label_misses_later_safety_updates(warfarin_v20, warfarin_2012):
    """A 2012 warfarin label lacks warnings added to warfarin labeling after 2012."""
    r = compare_labels(warfarin_v20, warfarin_2012)
    missing = " ".join(f.reference_text or "" for f in r.findings if f.kind == "missing").lower()
    assert "calciphylaxis" in missing
    assert "acute kidney injury" in missing
    assert r.lag_days == (date(2026, 1, 2) - date(2012, 9, 4)).days
    assert 0.8 < r.parity < 0.95
    assert r.severity >= Severity.MAJOR
    tier1 = [p for p in r.pools if p.tier == 1]
    assert any(p.pool == "boxed_warning" and p.parity == 1.0 for p in tier1)
    assert "description" in r.skipped_pools
    json.dumps(r.to_dict())


def test_flat_label_is_comparable(warfarin_v20, warfarin_flat):
    r = compare_labels(warfarin_v20, warfarin_flat)
    assert {p.pool for p in r.pools} >= {
        "boxed_warning",
        "warnings_precautions",
        "adverse_reactions",
    }
    assert r.parity > 0.7


def test_rmc_after_follower_date(warfarin_2012):
    assert _rmc_after(warfarin_2012, date(2011, 1, 1))
    assert _rmc_after(warfarin_2012, date(2012, 1, 1)) == []
    assert _rmc_after(warfarin_2012, None) == []
