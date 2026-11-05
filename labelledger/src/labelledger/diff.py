"""Section-aware redlines between two versions of a drug label.

Pipeline::

    old LabelDocument ─┐
                       ├─ align sections (exact key → fuzzy title → content) recursively
    new LabelDocument ─┘        │
                                ├─ per matched pair: line alignment, then word-level diff
                                ├─ hunks: added / removed / modified, editorial flag, signals
                                └─ severity triage per section (``severity.py``)

Everything here is deterministic. The output is the source of truth that AI
summaries are checked against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import Any, Literal

from . import __version__
from . import text as T
from .sections import label_of
from .severity import Severity, classify, hunk_signals
from .spl import LabelDocument, Section

SegmentOp = Literal["eq", "ins", "del"]
HunkKind = Literal["added", "removed", "modified"]
ChangeStatus = Literal["added", "removed", "modified"]

TITLE_MATCH = 0.80
CONTENT_MATCH = 0.60
LINE_PAIR_MATCH = 0.50
MOVE_MATCH = 0.90


@dataclass(frozen=True)
class Segment:
    op: SegmentOp
    text: str


@dataclass
class Hunk:
    hid: str
    kind: HunkKind
    old: str | None
    new: str | None
    segments: list[Segment]
    editorial: bool
    signals: list[str] = field(default_factory=list)

    @property
    def inserted(self) -> str:
        return T.clean(" ".join(s.text for s in self.segments if s.op == "ins"))

    @property
    def deleted(self) -> str:
        return T.clean(" ".join(s.text for s in self.segments if s.op == "del"))

    @property
    def words_changed(self) -> int:
        return len(T.content_tokens(self.inserted)) + len(T.content_tokens(self.deleted))

    def evidence_text(self) -> str:
        """All text a summary may quote for this hunk (old and new lines)."""
        return "\n".join(x for x in (self.old, self.new) if x)


@dataclass
class SectionChange:
    cid: str
    status: ChangeStatus
    path: list[str]
    family: str
    tier: int
    code: str | None
    old_sid: str | None
    new_sid: str | None
    old_title: str | None
    new_title: str | None
    hunks: list[Hunk]
    severity: Severity
    reasons: list[str]

    @property
    def heading(self) -> str:
        return " › ".join(self.path)

    @property
    def family_label(self) -> str:
        return label_of(self.family)

    @property
    def words_added(self) -> int:
        return sum(len(T.content_tokens(h.inserted)) for h in self.hunks)

    @property
    def words_removed(self) -> int:
        return sum(len(T.content_tokens(h.deleted)) for h in self.hunks)

    @property
    def substantive(self) -> list[Hunk]:
        return [h for h in self.hunks if not h.editorial]


@dataclass(frozen=True)
class DocRef:
    set_id: str
    version: int
    document_id: str
    effective_date: date | None
    labeler: str | None
    title: str
    sha256: str
    application_numbers: tuple[str, ...]
    format_kind: str

    @classmethod
    def of(cls, doc: LabelDocument) -> DocRef:
        return cls(
            set_id=doc.set_id,
            version=doc.version,
            document_id=doc.document_id,
            effective_date=doc.effective_date,
            labeler=doc.labeler,
            title=doc.display_title,
            sha256=doc.sha256,
            application_numbers=doc.application_numbers,
            format_kind=doc.format_kind,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "version": self.version,
            "document_id": self.document_id,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "labeler": self.labeler,
            "title": self.title,
            "sha256": self.sha256,
            "application_numbers": list(self.application_numbers),
            "format": self.format_kind,
        }


@dataclass
class LabelDiff:
    old: DocRef
    new: DocRef
    changes: list[SectionChange]
    recent_major_changes: list[str]
    generated_at: str
    engine_version: str = __version__

    @property
    def same_set(self) -> bool:
        return self.old.set_id == self.new.set_id

    @property
    def severity(self) -> Severity:
        return max((c.severity for c in self.changes), default=Severity.NONE)

    @property
    def counts(self) -> dict[str, int]:
        out = {s.label: 0 for s in Severity if s is not Severity.NONE}
        for c in self.changes:
            if c.severity is not Severity.NONE:
                out[c.severity.label] += 1
        return out

    @property
    def material_changes(self) -> list[SectionChange]:
        return [c for c in self.changes if c.severity >= Severity.MINOR]

    def hunk(self, hid: str) -> Hunk | None:
        for c in self.changes:
            for h in c.hunks:
                if h.hid == hid:
                    return h
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": {"name": "labelledger", "version": self.engine_version},
            "generated_at": self.generated_at,
            "old": self.old.to_dict(),
            "new": self.new.to_dict(),
            "same_set": self.same_set,
            "severity": self.severity.label,
            "counts": self.counts,
            "recent_major_changes": self.recent_major_changes,
            "changes": [
                {
                    "id": c.cid,
                    "status": c.status,
                    "section": c.heading,
                    "family": c.family,
                    "tier": c.tier,
                    "code": c.code,
                    "severity": c.severity.label,
                    "reasons": c.reasons,
                    "old_title": c.old_title,
                    "new_title": c.new_title,
                    "words_added": c.words_added,
                    "words_removed": c.words_removed,
                    "hunks": [
                        {
                            "id": h.hid,
                            "kind": h.kind,
                            "editorial": h.editorial,
                            "old": h.old,
                            "new": h.new,
                            "inserted": h.inserted,
                            "deleted": h.deleted,
                            "signals": h.signals,
                            "segments": [{"op": s.op, "text": s.text} for s in h.segments],
                        }
                        for h in c.hunks
                    ],
                }
                for c in self.changes
            ],
        }


# ---------------------------------------------------------------------------
# Word-level diff of a line pair
# ---------------------------------------------------------------------------


_PIECE = re.compile(r"\s+|\d+(?:[.,]\d+)*%?|\w+(?:['’]\w+)*|[^\w\s]")


def word_segments(old: str, new: str) -> list[Segment]:
    """Word-level redline of two lines.

    Invariant (property-tested): joining ``eq`` + ``ins`` segment texts gives
    exactly ``new``, and ``eq`` + ``del`` gives exactly ``old``. Whitespace is
    diffed as tokens so spacing is never lost; whitespace sandwiched between
    two edits is folded into the edit so a rewritten phrase reads as one
    deletion followed by one insertion rather than interleaved fragments.
    """
    a, b = _PIECE.findall(old), _PIECE.findall(new)
    sm = SequenceMatcher(None, a, b, autojunk=False)
    raw: list[Segment] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            raw.append(Segment("eq", "".join(b[j1:j2])))
            continue
        if tag in ("delete", "replace"):
            raw.append(Segment("del", "".join(a[i1:i2])))
        if tag in ("insert", "replace"):
            raw.append(Segment("ins", "".join(b[j1:j2])))
    # Fold whitespace-only equal runs that sit between edits into the edits.
    folded: list[Segment] = []
    for k, seg in enumerate(raw):
        between_edits = (
            seg.op == "eq"
            and not seg.text.strip()
            and 0 < k < len(raw) - 1
            and raw[k - 1].op != "eq"
            and raw[k + 1].op != "eq"
        )
        if between_edits:
            folded.extend((Segment("del", seg.text), Segment("ins", seg.text)))
        else:
            folded.append(seg)
    # Within each run of edits, emit all deletions, then all insertions.
    out: list[Segment] = []
    run_del: list[str] = []
    run_ins: list[str] = []

    def flush() -> None:
        if run_del:
            out.append(Segment("del", "".join(run_del)))
        if run_ins:
            out.append(Segment("ins", "".join(run_ins)))
        run_del.clear()
        run_ins.clear()

    for seg in folded:
        if seg.op == "eq":
            flush()
            out.append(seg)
        elif seg.op == "del":
            run_del.append(seg.text)
        else:
            run_ins.append(seg.text)
    flush()
    return [seg for seg in out if seg.text]


def _line_hunk(hid: str, old: str | None, new: str | None) -> Hunk:
    if old is None and new is not None:
        return Hunk(hid, "added", None, new, [Segment("ins", new)], False, hunk_signals("", new))
    if new is None and old is not None:
        return Hunk(hid, "removed", old, None, [Segment("del", old)], False, hunk_signals(old, ""))
    assert old is not None and new is not None
    editorial = T.is_editorial(old, new)
    signals = [] if editorial else hunk_signals(old, new)
    return Hunk(hid, "modified", old, new, word_segments(old, new), editorial, signals)


def diff_lines(old: list[str], new: list[str], prefix: str = "h") -> list[Hunk]:
    """Align two lists of lines and return the changed ones as hunks."""
    okeys = [T.compare_key(x) for x in old]
    nkeys = [T.compare_key(x) for x in new]
    sm = SequenceMatcher(None, okeys, nkeys, autojunk=False)
    pairs: list[tuple[str | None, str | None]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs.extend((a, b) for a, b in zip(old[i1:i2], new[j1:j2], strict=True) if a != b)
        elif tag == "delete":
            pairs.extend((a, None) for a in old[i1:i2])
        elif tag == "insert":
            pairs.extend((None, b) for b in new[j1:j2])
        else:
            pairs.extend(_pair_block(old[i1:i2], new[j1:j2]))
    return [_line_hunk(f"{prefix}{n}", a, b) for n, (a, b) in enumerate(pairs, start=1)]


def _pair_block(olds: list[str], news: list[str]) -> list[tuple[str | None, str | None]]:
    """Monotonic best-match pairing of lines inside a replaced block."""
    out: list[tuple[str | None, str | None]] = []
    j = 0
    for a in olds:
        best_k, best = -1, 0.0
        for k in range(j, len(news)):
            score = T.similarity(a, news[k])
            if score > best:
                best_k, best = k, score
        if best_k >= 0 and best >= LINE_PAIR_MATCH:
            out.extend((None, b) for b in news[j:best_k])
            out.append((a, news[best_k]))
            j = best_k + 1
        else:
            out.append((a, None))
    out.extend((None, b) for b in news[j:])
    return out


# ---------------------------------------------------------------------------
# Section alignment
# ---------------------------------------------------------------------------


@dataclass
class _Pair:
    old: Section | None
    new: Section | None
    path: list[str]


def _content_similarity(a: Section, b: Section) -> float:
    return T.similarity(a.full_text()[:4000], b.full_text()[:4000])


def _fuzzy_pairs(olds: list[Section], news: list[Section]) -> list[tuple[int, int]]:
    """Greedy best-first pairing of leftover sections by title, then content."""
    candidates: list[tuple[float, int, int]] = []
    for i, a in enumerate(olds):
        for j, b in enumerate(news):
            if a.family != b.family:
                continue
            score = 0.0
            if a.title and b.title:
                tscore = T.similarity(
                    T.split_heading_number(a.title)[1], T.split_heading_number(b.title)[1]
                )
                if tscore >= TITLE_MATCH:
                    score = 1.0 + tscore
            if score == 0.0:
                cscore = _content_similarity(a, b)
                if cscore >= CONTENT_MATCH:
                    score = cscore
            if score > 0:
                candidates.append((score, i, j))
    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_o: set[int] = set()
    used_n: set[int] = set()
    matched: list[tuple[int, int]] = []
    for _, i, j in candidates:
        if i in used_o or j in used_n:
            continue
        used_o.add(i)
        used_n.add(j)
        matched.append((i, j))
    return matched


def align_sections(
    olds: list[Section], news: list[Section], path: list[str] | None = None
) -> list[_Pair]:
    """Align sibling sections (and recursively their children)."""
    base = path or []
    sm = SequenceMatcher(None, [s.key for s in olds], [s.key for s in news], autojunk=False)
    top: list[_Pair] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            top.extend(
                _Pair(a, b, [*base, b.heading])
                for a, b in zip(olds[i1:i2], news[j1:j2], strict=True)
            )
            continue
        block_old, block_new = olds[i1:i2], news[j1:j2]
        matched = dict(_fuzzy_pairs(block_old, block_new))
        rev = {j: i for i, j in matched.items()}
        for i, a in enumerate(block_old):
            if i not in matched:
                top.append(_Pair(a, None, [*base, a.heading]))
        for j, b in enumerate(block_new):
            if j in rev:
                top.append(_Pair(block_old[rev[j]], b, [*base, b.heading]))
            else:
                top.append(_Pair(None, b, [*base, b.heading]))

    # Second chance: a section removed in one place and added in another with
    # near-identical content was moved, not deleted and re-written.
    removed = [p for p in top if p.new is None]
    added = [p for p in top if p.old is None]
    for gone in removed:
        assert gone.old is not None
        for fresh in added:
            if fresh.new is None or fresh.old is not None:
                continue
            if (
                gone.old.family == fresh.new.family
                and _content_similarity(gone.old, fresh.new) >= MOVE_MATCH
            ):
                fresh.old = gone.old
                gone.old = None
                break
    top = [p for p in top if p.old is not None or p.new is not None]

    out: list[_Pair] = []
    for p in top:
        out.append(p)
        if p.old is not None and p.new is not None:
            out.extend(align_sections(p.old.children, p.new.children, p.path))
        elif p.new is not None:
            out.extend(_Pair(None, s, [*p.path, *_subpath(p.new, s)]) for s in _descendants(p.new))
        elif p.old is not None:
            out.extend(_Pair(s, None, [*p.path, *_subpath(p.old, s)]) for s in _descendants(p.old))
    return out


def _descendants(section: Section) -> list[Section]:
    return [s for s in section.walk() if s is not section]


def _subpath(root: Section, target: Section) -> list[str]:
    def find(node: Section, trail: list[str]) -> list[str] | None:
        for child in node.children:
            if child is target:
                return [*trail, child.heading]
            hit = find(child, [*trail, child.heading])
            if hit is not None:
                return hit
        return None

    return find(root, []) or [target.heading]


# ---------------------------------------------------------------------------
# Document diff
# ---------------------------------------------------------------------------


def _change_for(pair: _Pair, cid: str) -> SectionChange | None:
    old, new = pair.old, pair.new
    ref = new if new is not None else old
    assert ref is not None
    if old is None or new is None:
        lines = ref.lines
        if not lines and not ref.title:
            return None
        status: ChangeStatus = "added" if old is None else "removed"
        hunks = [
            _line_hunk(
                f"{cid}.h{n}",
                None if status == "added" else line,
                line if status == "added" else None,
            )
            for n, line in enumerate(lines, start=1)
        ]
        severity, reasons = classify(
            status=status,
            family=ref.family,
            tier=ref.tier,
            substantive_hunks=len(hunks),
            editorial_hunks=0,
            words_changed=sum(h.words_changed for h in hunks),
            signals=[s for h in hunks for s in h.signals],
            title_changed=False,
            title_editorial=False,
        )
        return SectionChange(
            cid=cid,
            status=status,
            path=pair.path,
            family=ref.family,
            tier=ref.tier,
            code=ref.code,
            old_sid=old.sid if old else None,
            new_sid=new.sid if new else None,
            old_title=old.title if old else None,
            new_title=new.title if new else None,
            hunks=hunks,
            severity=severity,
            reasons=reasons,
        )

    hunks = diff_lines(old.lines, new.lines, prefix=f"{cid}.h")
    title_changed = old.title != new.title
    if not hunks and not title_changed:
        return None
    substantive = [h for h in hunks if not h.editorial]
    title_editorial = T.title_key(old.title) == T.title_key(new.title) if title_changed else True
    severity, reasons = classify(
        status="modified",
        family=new.family,
        tier=new.tier,
        substantive_hunks=len(substantive),
        editorial_hunks=len(hunks) - len(substantive),
        words_changed=sum(h.words_changed for h in substantive),
        signals=[s for h in substantive for s in h.signals],
        title_changed=title_changed,
        title_editorial=title_editorial,
    )
    if severity is Severity.NONE:
        return None
    return SectionChange(
        cid=cid,
        status="modified",
        path=pair.path,
        family=new.family,
        tier=new.tier,
        code=new.code,
        old_sid=old.sid,
        new_sid=new.sid,
        old_title=old.title,
        new_title=new.title,
        hunks=hunks,
        severity=severity,
        reasons=reasons,
    )


def diff_documents(
    old: LabelDocument, new: LabelDocument, *, now: datetime | None = None
) -> LabelDiff:
    """Redline ``new`` against ``old`` section by section."""
    pairs = align_sections(old.sections, new.sections)
    changes: list[SectionChange] = []
    for pair in pairs:
        change = _change_for(pair, f"c{len(changes) + 1}")
        if change is not None:
            changes.append(change)
    stamp = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    return LabelDiff(
        old=DocRef.of(old),
        new=DocRef.of(new),
        changes=changes,
        recent_major_changes=new.recent_major_changes(),
        generated_at=stamp,
    )
