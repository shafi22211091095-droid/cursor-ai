"""Text normalisation, tokenisation and sentence splitting for label text.

Two kinds of normalisation are kept deliberately separate:

* :func:`clean` produces the text we *display and store*: whitespace collapsed,
  nothing else altered. Evidence quotes and redlines are built from it, so a
  reviewer can find every quoted string verbatim in the source label.
* :func:`compare_key` produces the text we *compare*: typographic variants
  folded (curly quotes, dashes, non-breaking spaces), case folded, trademark
  symbols and cross-reference brackets removed. Two lines with equal keys are
  the same statement for diffing purposes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_WS = re.compile(r"\s+")
_TRANSLATE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "′": "'",
        "–": "-",
        "—": "-",
        "‒": "-",
        "−": "-",
        " ": " ",
        " ": " ",
        " ": " ",
        "•": " ",
        "®": "",
        "™": "",
        "©": "",
    }
)
# "[see Warnings and Precautions (5.1)]" and "(5.1, 5.4)" style cross references
_XREF = re.compile(r"\[\s*see[^\]]*\]|\(\s*\d+(?:\.\d+)*(?:\s*,\s*\d+(?:\.\d+)*)*\s*\)", re.I)
_PUNCT = re.compile(r"[^\w\s]")
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*%?|\w+(?:['’]\w+)*|[^\w\s]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# "Table 2", "Figure 1", "Fig. 3" — document furniture, not clinical values
_FURNITURE = re.compile(r"\b(?:table|figure|fig\.?)\s*\d+[a-z]?", re.I)
_HEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*[.)]?\s+")

# Sentence boundary: terminal punctuation followed by space and an uppercase
# letter, digit or opening bracket. Abbreviations common in labels are
# protected first so "e.g. Warfarin" does not split.
_ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "vs.",
    "approx.",
    "Fig.",
    "fig.",
    "No.",
    "no.",
    "Dr.",
    "et al.",
    "U.S.",
    "Inc.",
    "Ltd.",
    "Co.",
    "St.",
    "mg.",
    "Ph.",
)
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+(?=[A-Z0-9(\[•\"'])")
_PLACEHOLDER = "\u0000"


def clean(text: str) -> str:
    """Collapse whitespace and strip; keep every other character as published."""
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()


def fold(text: str) -> str:
    """Fold typographic variants without removing words."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).translate(_TRANSLATE)).strip()


def compare_key(text: str) -> str:
    """Key under which two strings count as the same statement."""
    folded = fold(text)
    folded = _XREF.sub(" ", folded)
    folded = _PUNCT.sub(" ", folded.lower())
    return _WS.sub(" ", folded).strip()


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int

    @property
    def key(self) -> str:
        return fold(self.text).lower()


def tokenize(text: str) -> list[Token]:
    return [Token(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def content_tokens(text: str) -> list[str]:
    """Lower-cased word and number tokens (no punctuation) used for coverage."""
    return [t.key for t in tokenize(text) if t.text[0].isalnum()]


def numbers(text: str) -> list[str]:
    """Numeric values in ``text``, ignoring cross-reference section numbers like ``(5.1)``."""
    stripped = _FURNITURE.sub(" ", _XREF.sub(" ", fold(text)))
    return [n.replace(",", "") for n in _NUMBER.findall(stripped)]


def key_tokens(text: str) -> list[str]:
    """Tokens of the comparison key (cross-references and punctuation removed)."""
    return compare_key(text).split()


def split_heading_number(title: str) -> tuple[str | None, str]:
    """Split ``"5.4 Acute Kidney Injury"`` into ``("5.4", "Acute Kidney Injury")``."""
    m = _HEADING_NUMBER.match(title)
    if not m:
        return None, title.strip()
    return m.group(1), title[m.end() :].strip()


def title_key(title: str) -> str:
    _, rest = split_heading_number(title)
    return compare_key(rest)


def split_sentences(text: str) -> list[str]:
    """Split a block of label text into sentences, keeping list items whole."""
    sentences: list[str] = []
    for line in text.splitlines():
        line = clean(line)
        if not line:
            continue
        protected = line
        for abbr in _ABBREVIATIONS:
            protected = protected.replace(abbr, abbr.replace(".", _PLACEHOLDER))
        for part in _SENTENCE_END.split(protected):
            part = part.replace(_PLACEHOLDER, ".").strip()
            if part:
                sentences.append(part)
    return sentences


def is_editorial(old: str, new: str) -> bool:
    """True when two strings differ only in formatting, case, punctuation or cross-references."""
    return compare_key(old) == compare_key(new)


def similarity(a: str, b: str) -> float:
    """Order-sensitive similarity of two strings' comparison keys, 0..1."""
    from rapidfuzz import fuzz

    return float(fuzz.ratio(compare_key(a), compare_key(b))) / 100.0
