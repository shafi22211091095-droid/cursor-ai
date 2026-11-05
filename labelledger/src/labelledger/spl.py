"""Safe parsing of HL7 Structured Product Labeling (SPL) XML into a section tree.

SPL is the XML format in which every US drug label is submitted to FDA and
published on DailyMed. This module turns one SPL document into a
:class:`LabelDocument`: document metadata (set id, version, effective date,
labeler, application numbers, products) plus an ordered tree of
:class:`Section` objects whose text is rendered line by line — one line per
paragraph, list item or table row — which is the unit the diff engine aligns.

Security: SPL files are untrusted input (customers upload them). Documents
containing a DOCTYPE or entity declaration are rejected outright, the parser
never resolves entities or touches the network, and input size is capped. This
closes XXE and entity-expansion attacks without relying on parser defaults.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lxml import etree

from . import text as T
from .sections import FAMILIES, family_for, label_of, tier_of

NS = "urn:hl7-org:v3"
FDA_APPLICATION_OID = "2.16.840.1.113883.3.150"
NDC_OID = "2.16.840.1.113883.6.69"
ACTIVE_INGREDIENT_CLASSES = {"ACTIB", "ACTIM", "ACTIR"}
MAX_BYTES = 50 * 1024 * 1024
_FORBIDDEN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
_SKIP_TAGS = {"renderMultiMedia", "col", "colgroup", "observationMedia"}


class SPLParseError(ValueError):
    """The input is not a usable SPL document."""


class UnsafeXMLError(SPLParseError):
    """The input contains constructs we refuse to parse (DTDs, entities)."""


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _local(el: etree._Element) -> str:
    tag: object = el.tag
    if not isinstance(tag, str):  # comments and processing instructions
        return ""
    return tag.rsplit("}", 1)[-1]


def _attr(el: etree._Element | None, name: str) -> str | None:
    if el is None:
        return None
    value = el.get(name)
    return value if value else None


_BLOCKISH = {"paragraph", "item", "list", "caption", "footnote", "br", "tr", "td", "th", "table"}


def _flatten(el: etree._Element | None) -> str:
    """Document-order text of an element on one line.

    Inline markup joins without spaces (``K<sub>1</sub>`` -> ``K1``); block-level
    markup (paragraphs, cells, line breaks) is separated by a space.
    """
    if el is None:
        return ""
    parts: list[str] = []

    def walk(node: etree._Element) -> None:
        tag = _local(node)
        if tag in _SKIP_TAGS:
            return
        block = tag in _BLOCKISH
        if block:
            parts.append(" ")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if block:
            parts.append(" ")

    walk(el)
    return T.clean("".join(parts))


def _parse_date(value: str | None) -> date | None:
    if not value or len(value) < 8 or not value[:8].isdigit():
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------


class _Renderer:
    """Render an SPL ``<text>`` element to display lines.

    One output line per paragraph, list item, caption or table row. Inline
    markup (bold, sub/superscript, links) is flattened into the line.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._prefix = ""

    def render(self, el: etree._Element) -> list[str]:
        self._inline(el)
        self._flush()
        return self.lines

    def _flush(self) -> None:
        line = T.clean("".join(self._buf))
        self._buf = []
        if line:
            self.lines.append(self._prefix + line)
            self._prefix = ""

    def _inline(self, el: etree._Element) -> None:
        if el.text:
            self._buf.append(el.text)
        for child in el:
            self._child(child)
            if child.tail:
                self._buf.append(child.tail)

    def _child(self, el: etree._Element) -> None:
        tag = _local(el)
        if not tag or tag in _SKIP_TAGS:
            return
        if tag == "br":
            self._flush()
        elif tag == "list":
            self._flush()
            for item in el:
                if _local(item) == "item":
                    self._flush()
                    self._prefix = "• "
                    self._inline(item)
                    self._flush()
                elif _local(item) == "caption":
                    self._flush()
                    self._inline(item)
                    self._flush()
            self._flush()
        elif tag == "table":
            self._flush()
            caption = el.find(_q("caption"))
            if caption is not None:
                self._buf.append(_flatten(caption))
                self._flush()
            for row in el.iter(_q("tr")):
                cells = [_flatten(c) for c in row if _local(c) in ("td", "th")]
                line = " | ".join(c for c in cells if c)
                if line:
                    self.lines.append(line)
            footnotes = [_flatten(f) for f in el.iter(_q("footnote"))]
            for note in footnotes:
                if note:
                    self.lines.append(f"[footnote] {note}")
        elif tag in ("paragraph", "caption"):
            self._flush()
            self._inline(el)
            self._flush()
        elif tag == "footnote":
            self._flush()
            self._buf.append("[footnote] ")
            self._inline(el)
            self._flush()
        else:  # content, sub, sup, span, linkHtml, unknown inline markup
            self._inline(el)


def render_text(el: etree._Element | None) -> list[str]:
    if el is None:
        return []
    return _Renderer().render(el)


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------


@dataclass
class Section:
    sid: str
    code: str | None
    code_name: str | None
    title: str
    family: str
    depth: int
    lines: list[str]
    highlights: list[str] = field(default_factory=list)
    children: list[Section] = field(default_factory=list)
    effective_date: date | None = None
    uuid: str | None = None

    @property
    def tier(self) -> int:
        return tier_of(self.family)

    @property
    def number(self) -> str | None:
        return T.split_heading_number(self.title)[0]

    @property
    def heading(self) -> str:
        """Human-readable heading: the title, else the code's display name."""
        if self.title:
            return self.title
        if self.code_name:
            return self.code_name.title()
        return label_of(self.family)

    @property
    def key(self) -> str:
        """Identity used to align this section across versions."""
        tkey = T.title_key(self.title) if self.title else ""
        return f"{self.code or '-'}|{tkey}"

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def walk(self) -> Iterator[Section]:
        yield self
        for child in self.children:
            yield from child.walk()

    def full_text(self) -> str:
        return "\n".join(line for s in self.walk() for line in s.lines)


@dataclass(frozen=True)
class Product:
    name: str
    generic_name: str | None
    form: str | None
    ndc: str | None
    active_ingredients: tuple[str, ...]


@dataclass
class LabelDocument:
    document_id: str
    set_id: str
    version: int
    effective_date: date | None
    doc_type_code: str | None
    doc_type_name: str | None
    title: str
    labeler: str | None
    application_numbers: tuple[str, ...]
    products: tuple[Product, ...]
    sections: list[Section]
    sha256: str
    source: str = ""

    def iter_sections(self) -> Iterator[Section]:
        for s in self.sections:
            yield from s.walk()

    @property
    def display_title(self) -> str:
        names = sorted({p.name for p in self.products if p.name})
        if names:
            return ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        short = T.clean(self.title)
        return short[:120] if short else self.set_id

    @property
    def generic_names(self) -> tuple[str, ...]:
        return tuple(sorted({p.generic_name for p in self.products if p.generic_name}))

    @property
    def format_kind(self) -> str:
        codes = {s.code for s in self.iter_sections()}
        if "43685-7" in codes or any(s.highlights for s in self.iter_sections()):
            return "PLR"
        if codes & {"34071-1", "42232-9"}:
            return "non-PLR"
        return "unknown"

    def recent_major_changes(self) -> list[str]:
        out: list[str] = []
        for s in self.iter_sections():
            if s.family == "recent_major_changes":
                out.extend(s.lines + s.highlights)
        return [T.clean(x) for x in out if T.clean(x)]

    def families_present(self) -> list[str]:
        seen: list[str] = []
        for s in self.iter_sections():
            if s.family not in seen:
                seen.append(s.family)
        return seen


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
    )


def _parse_section(el: etree._Element, sid: str, depth: int, parent_family: str | None) -> Section:
    code_el = el.find(_q("code"))
    code = _attr(code_el, "code")
    code_name = _attr(code_el, "displayName")
    family = family_for(code, parent_family)
    if family not in FAMILIES:
        family = "other"
    title = _flatten(el.find(_q("title")))
    lines = render_text(el.find(_q("text")))
    highlights: list[str] = []
    for excerpt in el.findall(_q("excerpt")):
        for hl in excerpt.iter(_q("highlight")):
            highlights.extend(render_text(hl.find(_q("text"))))
    section = Section(
        sid=sid,
        code=code,
        code_name=code_name,
        title=title,
        family=family,
        depth=depth,
        lines=lines,
        highlights=highlights,
        effective_date=_parse_date(_attr(el.find(_q("effectiveTime")), "value")),
        uuid=_attr(el.find(_q("id")), "root"),
    )
    index = 0
    for comp in el.findall(_q("component")):
        child = comp.find(_q("section"))
        if child is None:
            continue
        index += 1
        section.children.append(_parse_section(child, f"{sid}.{index}", depth + 1, family))
    return section


def _products(root: etree._Element) -> tuple[Product, ...]:
    products: list[Product] = []
    for outer in root.iter(_q("manufacturedProduct")):
        inner = outer.find(_q("manufacturedProduct"))
        if inner is None:
            continue
        code = inner.find(_q("code"))
        ndc = _attr(code, "code") if _attr(code, "codeSystem") == NDC_OID else None
        actives = tuple(
            _flatten(ing.find(f"{_q('ingredientSubstance')}/{_q('name')}"))
            for ing in inner.findall(_q("ingredient"))
            if ing.get("classCode") in ACTIVE_INGREDIENT_CLASSES
        )
        products.append(
            Product(
                name=_flatten(inner.find(_q("name"))),
                generic_name=_flatten(
                    inner.find(f"{_q('asEntityWithGeneric')}/{_q('genericMedicine')}/{_q('name')}")
                )
                or None,
                form=_attr(inner.find(_q("formCode")), "displayName"),
                ndc=ndc,
                active_ingredients=tuple(a for a in actives if a),
            )
        )
    return tuple(products)


def _application_numbers(root: etree._Element) -> tuple[str, ...]:
    found: set[str] = set()
    for approval in root.iter(_q("approval")):
        ident = approval.find(_q("id"))
        if (
            ident is not None
            and ident.get("root") == FDA_APPLICATION_OID
            and ident.get("extension")
        ):
            found.add(str(ident.get("extension")).upper())
    return tuple(sorted(found))


def parse_spl(data: bytes, *, source: str = "") -> LabelDocument:
    """Parse SPL XML bytes. Raises :class:`SPLParseError` on unusable input."""
    if len(data) > MAX_BYTES:
        raise SPLParseError(f"document is {len(data):,} bytes; limit is {MAX_BYTES:,}")
    if _FORBIDDEN.search(data):
        raise UnsafeXMLError("DOCTYPE/ENTITY declarations are not allowed in SPL input")
    try:
        root = etree.fromstring(data, parser=_parser())
    except etree.XMLSyntaxError as exc:
        raise SPLParseError(f"not well-formed XML: {exc}") from exc
    if root.tag != _q("document"):
        raise SPLParseError("root element is not an HL7 v3 <document>; is this an SPL file?")

    set_id = _attr(root.find(_q("setId")), "root")
    document_id = _attr(root.find(_q("id")), "root")
    if not set_id or not document_id:
        raise SPLParseError("SPL document is missing setId or id")
    version_raw = _attr(root.find(_q("versionNumber")), "value") or "0"
    try:
        version = int(version_raw)
    except ValueError as exc:
        raise SPLParseError(f"invalid versionNumber {version_raw!r}") from exc

    doc_code = root.find(_q("code"))
    labeler = _flatten(
        root.find(
            f"{_q('author')}/{_q('assignedEntity')}/{_q('representedOrganization')}/{_q('name')}"
        )
    )
    body = root.find(f"{_q('component')}/{_q('structuredBody')}")
    sections: list[Section] = []
    if body is not None:
        index = 0
        for comp in body.findall(_q("component")):
            sec = comp.find(_q("section"))
            if sec is None:
                continue
            index += 1
            sections.append(_parse_section(sec, str(index), 1, None))

    return LabelDocument(
        document_id=document_id,
        set_id=set_id,
        version=version,
        effective_date=_parse_date(_attr(root.find(_q("effectiveTime")), "value")),
        doc_type_code=_attr(doc_code, "code"),
        doc_type_name=_attr(doc_code, "displayName"),
        title=_flatten(root.find(_q("title"))),
        labeler=labeler or None,
        application_numbers=_application_numbers(root),
        products=_products(root),
        sections=sections,
        sha256=hashlib.sha256(data).hexdigest(),
        source=source,
    )


def parse_spl_file(path: str | Path) -> LabelDocument:
    p = Path(path)
    return parse_spl(p.read_bytes(), source=str(p))
