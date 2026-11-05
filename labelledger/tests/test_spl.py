from __future__ import annotations

from datetime import date

import pytest

from labelledger import spl
from labelledger.spl import SPLParseError, UnsafeXMLError, parse_spl

from .conftest import WARFARIN_2026


def test_metadata_of_real_plr_label(warfarin_v20):
    d = warfarin_v20
    assert d.set_id == "1244c338-9140-425a-bf2a-9da6b4ac0287"
    assert d.version == 20
    assert d.effective_date == date(2026, 1, 2)
    assert d.labeler == "REMEDYREPACK INC."
    assert d.application_numbers == ("ANDA040616",)
    assert d.doc_type_name == "HUMAN PRESCRIPTION DRUG LABEL"
    assert d.display_title == "Warfarin Sodium"
    assert d.generic_names == ("Warfarin Sodium",)
    assert d.format_kind == "PLR"
    assert len(d.sha256) == 64


def test_section_tree_families_and_inheritance(warfarin_v20):
    by_title = {s.title: s for s in warfarin_v20.iter_sections() if s.title}
    aki = by_title["5.4 Acute Kidney Injury"]
    assert aki.code == "42229-5"  # unclassified ...
    assert aki.family == "warnings_precautions"  # ... inherits its parent's family
    assert aki.tier == 1
    assert aki.number == "5.4"
    assert by_title["WARNING: BLEEDING RISK"].family == "boxed_warning"
    assert by_title["11 DESCRIPTION"].tier == 3


def test_text_rendering_lists_tables_and_highlights(warfarin_v20):
    boxed = next(s for s in warfarin_v20.iter_sections() if s.family == "boxed_warning")
    assert all(line.startswith("• ") for line in boxed.lines)
    assert "major or fatal bleeding" in boxed.lines[0]
    # Highlights (excerpts) are captured separately and never mixed into the FPI text.
    assert boxed.highlights and "See full prescribing information" in " ".join(boxed.highlights)
    assert not any("See full prescribing information" in line for line in boxed.lines)
    dosing = next(s for s in warfarin_v20.iter_sections() if s.title.startswith("2.3"))
    assert any(" | " in line for line in dosing.lines), "table rows render as cell | cell"


def test_old_format_label(metformin):
    assert metformin.format_kind == "non-PLR"
    boxed = next(s for s in metformin.iter_sections() if s.family == "boxed_warning")
    assert boxed.depth == 2  # nested under WARNINGS in the old format
    assert "lactic" in boxed.full_text().lower()


def test_recent_major_changes(warfarin_2012):
    rmc = warfarin_2012.recent_major_changes()
    assert any("Contraindications" in r and "10/2011" in r for r in rmc)


def test_rejects_doctype_and_entities():
    payload = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><document xmlns="urn:hl7-org:v3">&e;</document>'
    with pytest.raises(UnsafeXMLError):
        parse_spl(payload)


def test_rejects_non_spl_and_malformed():
    with pytest.raises(SPLParseError, match="not an HL7"):
        parse_spl(b"<html><body>hi</body></html>")
    with pytest.raises(SPLParseError, match="well-formed"):
        parse_spl(b"<document xmlns='urn:hl7-org:v3'><unclosed></document>")
    with pytest.raises(SPLParseError, match="setId"):
        parse_spl(b"<document xmlns='urn:hl7-org:v3'><id root='x'/></document>")
    with pytest.raises(SPLParseError, match="versionNumber"):
        parse_spl(
            b"<document xmlns='urn:hl7-org:v3'><id root='x'/><setId root='y'/>"
            b"<versionNumber value='abc'/></document>"
        )


def test_size_limit(monkeypatch):
    monkeypatch.setattr(spl, "MAX_BYTES", 100)
    with pytest.raises(SPLParseError, match="limit"):
        parse_spl(WARFARIN_2026.read_bytes())


def test_minimal_document_without_body():
    doc = parse_spl(
        b"<document xmlns='urn:hl7-org:v3'><id root='d'/><setId root='s'/><versionNumber value='3'/>"
        b"<effectiveTime value='2026xx01'/><title>T</title></document>"
    )
    assert doc.version == 3 and doc.sections == [] and doc.effective_date is None
    assert doc.display_title == "T" and doc.format_kind == "unknown"
