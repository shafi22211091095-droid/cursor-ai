"""Periodic digest of recorded label changes (the weekly email / newsletter body)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .severity import Severity
from .store import Store

_ORDER = {s.label: int(s) for s in Severity}


@dataclass
class DigestEntry:
    change_id: int
    set_id: str
    title: str
    labeler: str | None
    from_version: int
    to_version: int
    severity: str
    counts: dict[str, int]
    headline: str
    top_sections: list[str]


def build_digest(
    store: Store, since: str, *, min_severity: Severity = Severity.MINOR
) -> list[DigestEntry]:
    entries: list[DigestEntry] = []
    for row in store.changes(since=since, limit=1000):
        if _ORDER.get(row["severity"], 0) < int(min_severity):
            continue
        change = store.change(row["id"])
        assert change is not None
        payload: dict[str, Any] = json.loads(change["summary_json"] or change["report_json"])
        summary = payload.get("summary") or {}
        sections = [
            c["section"]
            for c in sorted(payload.get("changes", []), key=lambda c: -_ORDER.get(c["severity"], 0))
            if _ORDER.get(c["severity"], 0) >= int(Severity.MAJOR)
        ][:3]
        entries.append(
            DigestEntry(
                change_id=row["id"],
                set_id=row["set_id"],
                title=row["title"] or row["set_id"],
                labeler=row["labeler"],
                from_version=row["from_version"],
                to_version=row["to_version"],
                severity=row["severity"],
                counts=row["counts"],
                headline=summary.get("headline", ""),
                top_sections=sections,
            )
        )
    entries.sort(key=lambda e: (-_ORDER.get(e.severity, 0), e.title))
    return entries


def digest_markdown(entries: list[DigestEntry], since: str, base_url: str = "") -> str:
    lines = [f"# Reference label changes since {since[:10]}", ""]
    if not entries:
        lines.append("No material changes in this period.")
        return "\n".join(lines) + "\n"
    by_sev: dict[str, int] = {}
    for e in entries:
        by_sev[e.severity] = by_sev.get(e.severity, 0) + 1
    lines.append(
        f"{len(entries)} label(s) changed — "
        + ", ".join(f"{n} {s}" for s, n in sorted(by_sev.items(), key=lambda t: -_ORDER[t[0]]))
    )
    lines.append("")
    for e in entries:
        link = f"{base_url}/changes/{e.change_id}" if base_url else f"change #{e.change_id}"
        lines.append(f"## [{e.severity.upper()}] {e.title} — v{e.from_version} → v{e.to_version}")
        if e.labeler:
            lines.append(f"_{e.labeler}_ · `{e.set_id}` · {link}")
        if e.headline:
            lines.append("")
            lines.append(e.headline)
        if e.top_sections:
            lines.append("")
            lines.extend(f"- {s}" for s in e.top_sections)
        lines.append("")
    lines.append("---")
    lines.append(
        "Decision support only. Severity is rules-based; verify against official labeling."
    )
    return "\n".join(lines) + "\n"
