from __future__ import annotations

from labelledger.ai import deterministic_summary
from labelledger.diff import Segment, diff_documents
from labelledger.drift import compare_labels
from labelledger.report import _md_segments, diff_to_markdown, drift_to_markdown, render_drift_html
from labelledger.severity import Severity


def test_markdown_segments_keep_spacing_outside_markers():
    segs = [
        Segment("eq", "usually 2 to"),
        Segment("del", " 5"),
        Segment("ins", " 4"),
        Segment("eq", " mg"),
    ]
    assert _md_segments(segs) == "usually 2 to ~~5~~ **4** mg"


def test_diff_markdown_contains_summary_and_redline(warfarin_v20, warfarin_v21):
    d = diff_documents(warfarin_v20, warfarin_v21)
    md = diff_to_markdown(d, deterministic_summary(d, notice="AI off"))
    assert md.startswith("# Redline: Warfarin Sodium (v20 → v21)")
    assert "> AI off" in md and "**Considerations for your labeling**" in md
    assert "~~5~~ **4**" in md
    assert "(editorial)" in md


def test_drift_markdown_and_html_filtering(warfarin_v20, warfarin_2012):
    r = compare_labels(warfarin_v20, warfarin_2012)
    md = drift_to_markdown(r, min_severity=Severity.MAJOR)
    assert "# Label drift" in md and "## Findings at major or above" in md
    assert "calciphylaxis" in md.lower()
    html = render_drift_html(r, min_severity=Severity.CRITICAL)
    assert "lower-severity finding(s) hidden" in html
    assert "<script" not in html.lower()
