from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from labelledger import text as T
from labelledger.diff import diff_documents, diff_lines, word_segments
from labelledger.severity import Severity

from .conftest import SYNTHETIC_EDITS


def _by_heading(diff):
    return {c.heading: c for c in diff.changes}


def test_identical_documents_have_no_changes(warfarin_v20):
    d = diff_documents(warfarin_v20, warfarin_v20)
    assert d.changes == [] and d.severity is Severity.NONE and d.same_set


def test_synthetic_revision_finds_exactly_the_planted_edits(warfarin_v20, warfarin_v21):
    d = diff_documents(warfarin_v20, warfarin_v21)
    got = {c.heading: (c.status, c.severity) for c in d.changes}
    assert got == {
        "2 DOSAGE AND ADMINISTRATION › 2.3 Initial and Maintenance Dosing": (
            "modified",
            Severity.CRITICAL,
        ),
        "5 WARNINGS AND PRECAUTIONS › 5.4 Acute Kidney Injury": ("modified", Severity.MAJOR),
        "5 WARNINGS AND PRECAUTIONS › 5.10 Serious Hypersensitivity Reactions": (
            "added",
            Severity.MAJOR,
        ),
        "6 ADVERSE REACTIONS": ("modified", Severity.MAJOR),
        "11 DESCRIPTION": ("modified", Severity.EDITORIAL),
    }
    assert d.severity is Severity.CRITICAL
    assert d.counts == {"editorial": 1, "minor": 0, "moderate": 0, "major": 3, "critical": 1}
    assert d.old.version == 20 and d.new.version == 21


def test_dose_change_is_a_word_level_redline(warfarin_v20, warfarin_v21):
    c = _by_heading(diff_documents(warfarin_v20, warfarin_v21))[
        "2 DOSAGE AND ADMINISTRATION › 2.3 Initial and Maintenance Dosing"
    ]
    (h,) = c.substantive
    assert (h.deleted, h.inserted) == ("5", "4")
    assert any(s.startswith("numbers changed: 5 → 4") for s in h.signals)
    assert "Numeric dosing values changed" in c.reasons


def test_added_sentence_and_removed_item(warfarin_v20, warfarin_v21):
    by = _by_heading(diff_documents(warfarin_v20, warfarin_v21))
    aki = by["5 WARNINGS AND PRECAUTIONS › 5.4 Acute Kidney Injury"]
    assert [h.kind for h in aki.hunks] == ["added"]
    assert aki.hunks[0].new == SYNTHETIC_EDITS["aki_sentence"]
    ar = by["6 ADVERSE REACTIONS"]
    assert [h.kind for h in ar.hunks] == ["removed"] and ar.hunks[0].old.startswith(
        "• Calciphylaxis"
    )


def test_editorial_change_is_flagged_not_hidden(warfarin_v20, warfarin_v21):
    desc = _by_heading(diff_documents(warfarin_v20, warfarin_v21))["11 DESCRIPTION"]
    assert all(h.editorial for h in desc.hunks) and desc.severity is Severity.EDITORIAL
    assert "Sodium Tablets" in desc.hunks[0].inserted


def test_diff_serialises_and_hunk_lookup(warfarin_v20, warfarin_v21):
    d = diff_documents(warfarin_v20, warfarin_v21)
    payload = json.loads(json.dumps(d.to_dict()))
    assert payload["severity"] == "critical" and payload["new"]["version"] == 21
    hid = d.changes[0].hunks[0].hid
    assert d.hunk(hid) is d.changes[0].hunks[0] and d.hunk("nope") is None
    assert len(d.material_changes) == 4


def test_cross_label_diff_is_marked(warfarin_v20, warfarin_2012):
    d = diff_documents(warfarin_2012, warfarin_v20)
    assert not d.same_set and d.changes


def test_reordered_and_moved_sections_are_not_reported_as_rewrites(warfarin_v20):
    import copy

    moved = copy.deepcopy(warfarin_v20)
    moved.sections.reverse()
    d = diff_documents(warfarin_v20, moved)
    assert all(c.status == "modified" or c.severity <= Severity.MINOR for c in d.changes)
    assert not [c for c in d.changes if c.status in ("added", "removed")]


def test_diff_lines_pairs_similar_lines():
    old = ["Take with food.", "Monitor INR weekly.", "Store at room temperature."]
    new = ["Take with or without food.", "Monitor INR every 2 weeks.", "Store at room temperature."]
    hunks = diff_lines(old, new)
    assert [h.kind for h in hunks] == ["modified", "modified"]
    assert hunks[1].inserted == "every 2 weeks"


words = st.lists(
    st.sampled_from(
        ["warfarin", "5", "mg", "daily", "not", "bleeding", "(", ")", ",", "INR", "2.5", "patients"]
    ),
    max_size=25,
)


@settings(max_examples=300, deadline=None)
@given(words, words)
def test_word_segments_reconstruct_both_sides(a, b):
    old, new = " ".join(a), " ".join(b)
    segs = word_segments(old, new)
    rebuilt_new = "".join(s.text for s in segs if s.op in ("eq", "ins"))
    rebuilt_old = "".join(s.text for s in segs if s.op in ("eq", "del"))
    assert T.clean(rebuilt_new) == T.clean(new)
    assert T.clean(rebuilt_old) == T.clean(old)
