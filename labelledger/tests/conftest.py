"""Shared fixtures.

Real SPL documents live in ``tests/fixtures/spl`` (provenance in
``tests/fixtures/PROVENANCE.md``). Version pairs of the *same* label were not
obtainable in the build sandbox, so :func:`synthetic_revision` derives a
"version 21" from the real version-20 warfarin label by applying a known,
documented set of edits. Tests assert that the engine finds exactly those
edits — which is a stronger test than an unknown real revision would be.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from labelledger.spl import LabelDocument, parse_spl

FIXTURES = Path(__file__).parent / "fixtures" / "spl"
WARFARIN_2026 = FIXTURES / "warfarin_remedyrepack_v20_2026.xml"
WARFARIN_2012 = FIXTURES / "warfarin_tya_v2_2012.xml"
WARFARIN_2015_FLAT = FIXTURES / "warfarin_northwind_v1_2015.xml"
METFORMIN = FIXTURES / "metformin_v13.xml"

NEW_SUBSECTION = """
               <component>
                  <section ID="LINK_synthetic_5_10">
                     <id root="00000000-0000-4000-8000-000000000510"/>
                     <code code="42229-5" codeSystem="2.16.840.1.113883.6.1" displayName="SPL UNCLASSIFIED SECTION"/>
                     <title>5.10 Serious Hypersensitivity Reactions</title>
                     <text>
                        <paragraph>Serious hypersensitivity reactions, including anaphylaxis, have been reported. Discontinue warfarin sodium if a serious hypersensitivity reaction occurs.</paragraph>
                     </text>
                     <effectiveTime value="20260415"/>
                  </section>
               </component>"""

#: The edits applied by synthetic_revision, for assertions and documentation.
SYNTHETIC_EDITS = {
    "dose": ("is usually 2 to 5 mg once daily", "is usually 2 to 4 mg once daily"),
    "aki_sentence": "Cases of fatal acute kidney injury have been reported in patients receiving warfarin sodium.",
    "new_section": "5.10 Serious Hypersensitivity Reactions",
    "removed_item": "Calciphylaxis",
    "editorial": ("Warfarin sodium tablets, USP contain", "Warfarin Sodium Tablets, USP contain"),
}


def synthetic_revision(xml: str) -> str:
    """Apply the SYNTHETIC_EDITS to the version-20 warfarin label."""
    out = xml.replace('<versionNumber value="20"/>', '<versionNumber value="21"/>', 1)
    out = out.replace('<effectiveTime value="20260102"/>', '<effectiveTime value="20260415"/>', 1)
    old_dose, new_dose = SYNTHETIC_EDITS["dose"]
    assert old_dose in out
    out = out.replace(old_dose, new_dose, 1)

    anchor = "<title>5.4 Acute Kidney Injury</title>"
    i = out.index(anchor)
    j = out.index("<text>", i) + len("<text>")
    out = out[:j] + f"\n<paragraph>{SYNTHETIC_EDITS['aki_sentence']}</paragraph>" + out[j:]

    k = out.index("<title>5.9 Endogenous Factors Affecting INR</title>")
    end = out.index("</component>", out.index("</section>", k)) + len("</component>")
    out = out[:end] + NEW_SUBSECTION + out[end:]

    # Remove the item from section 6's list, not the Highlights excerpt (which
    # the engine deliberately does not diff — Highlights restate the FPI).
    ar = out.index("<title>6 ADVERSE REACTIONS</title>")
    tail, n = re.subn(r"<item>Calciphylaxis\s.*?</item>", "", out[ar:], count=1, flags=re.S)
    assert n == 1
    out = out[:ar] + tail

    old_ed, new_ed = SYNTHETIC_EDITS["editorial"]
    assert old_ed in out
    return out.replace(old_ed, new_ed, 1)


@pytest.fixture(scope="session")
def warfarin_v20() -> LabelDocument:
    return parse_spl(WARFARIN_2026.read_bytes(), source=str(WARFARIN_2026))


@pytest.fixture(scope="session")
def warfarin_v21_bytes() -> bytes:
    return synthetic_revision(WARFARIN_2026.read_text(encoding="utf-8")).encode("utf-8")


@pytest.fixture(scope="session")
def warfarin_v21(warfarin_v21_bytes: bytes) -> LabelDocument:
    return parse_spl(warfarin_v21_bytes, source="synthetic-v21")


@pytest.fixture(scope="session")
def warfarin_2012() -> LabelDocument:
    return parse_spl(WARFARIN_2012.read_bytes(), source=str(WARFARIN_2012))


@pytest.fixture(scope="session")
def warfarin_flat() -> LabelDocument:
    return parse_spl(WARFARIN_2015_FLAT.read_bytes(), source=str(WARFARIN_2015_FLAT))


@pytest.fixture(scope="session")
def metformin() -> LabelDocument:
    return parse_spl(METFORMIN.read_bytes(), source=str(METFORMIN))
