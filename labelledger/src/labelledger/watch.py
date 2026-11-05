"""Ingestion and watchlist polling.

``ingest`` is the single entry point for a new SPL document, whether it was
uploaded, imported from disk or fetched from DailyMed: it stores the version
and, if an earlier version of the same label is stored, computes and records
the redline (which raises alerts for everyone watching that label).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .ai import SummaryResult, summarize
from .dailymed import DailyMedClient, DailyMedError
from .diff import LabelDiff, diff_documents
from .report import diff_to_json
from .spl import LabelDocument, SPLParseError, parse_spl
from .store import Store


@dataclass
class IngestResult:
    doc: LabelDocument
    created: bool
    change_id: int | None = None
    diff: LabelDiff | None = None
    summary: SummaryResult | None = None


def record_diff(store: Store, diff: LabelDiff, *, ai: bool = True) -> tuple[int, SummaryResult]:
    summary = summarize(diff, mode=None if ai else "off")
    change_id = store.record_change(
        diff.new.set_id,
        diff.old.version,
        diff.new.version,
        diff.severity.label,
        diff.counts,
        diff_to_json(diff),
        diff_to_json(diff, summary),
    )
    return change_id, summary


def ingest(store: Store, xml: bytes, *, source: str, ai: bool = True) -> IngestResult:
    doc = parse_spl(xml, source=source)
    created = store.save_version(doc, xml, source)
    result = IngestResult(doc=doc, created=created)
    if not created:
        return result
    previous = store.previous_version(doc.set_id, doc.version)
    if previous is None:
        return result
    diff = diff_documents(store.document(doc.set_id, previous), doc)
    result.diff = diff
    result.change_id, result.summary = record_diff(store, diff, ai=ai)
    return result


@dataclass
class PollOutcome:
    set_id: str
    status: Literal["unchanged", "first_capture", "new_version", "error"]
    version: int | None
    change_id: int | None = None
    severity: str | None = None
    message: str = ""


def poll(store: Store, client: DailyMedClient, *, ai: bool = True) -> list[PollOutcome]:
    """Check every watched label once; capture and diff any new version."""
    outcomes: list[PollOutcome] = []
    for set_id in store.watched_set_ids():
        try:
            history = client.history(set_id)
            remote = max((v.version for v in history), default=None)
            local = store.latest_version(set_id)
            if remote is not None and local is not None and remote <= local:
                store.touch_checked(set_id)
                outcomes.append(PollOutcome(set_id, "unchanged", local))
                continue
            result = ingest(store, client.current_xml(set_id), source="dailymed", ai=ai)
            store.touch_checked(set_id)
            if result.diff is not None:
                outcomes.append(
                    PollOutcome(
                        set_id,
                        "new_version",
                        result.doc.version,
                        result.change_id,
                        result.diff.severity.label,
                    )
                )
            elif result.created:
                outcomes.append(PollOutcome(set_id, "first_capture", result.doc.version))
            else:
                outcomes.append(PollOutcome(set_id, "unchanged", result.doc.version))
        except (DailyMedError, SPLParseError) as exc:
            outcomes.append(PollOutcome(set_id, "error", None, message=str(exc)))
    return outcomes
