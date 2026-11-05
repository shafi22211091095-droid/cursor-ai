"""Command-line interface.

Examples::

    labelledger diff old.xml new.xml --format html --out redline.html
    labelledger drift reference.xml generic.xml --min major
    labelledger ingest labels/*.xml
    labelledger watch add 1244c338-9140-425a-bf2a-9da6b4ac0287 --role reference
    labelledger poll
    labelledger digest --days 7
    labelledger serve
"""

from __future__ import annotations

import ipaddress
import os
import sys
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .ai import summarize
from .dailymed import DailyMedClient, DailyMedError
from .diff import diff_documents
from .digest import build_digest, digest_markdown
from .drift import compare_labels
from .report import (
    diff_to_json,
    diff_to_markdown,
    drift_to_json,
    drift_to_markdown,
    render_diff_html,
    render_drift_html,
)
from .severity import Severity
from .spl import SPLParseError, parse_spl_file
from .store import ROLES, Store
from .watch import ingest, poll

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="Version control and redlines for drug labels."
)
watch_app = typer.Typer(no_args_is_help=True, help="Manage the watchlist.")
app.add_typer(watch_app, name="watch")


class Fmt(StrEnum):
    md = "md"
    html = "html"
    json = "json"


class SevName(StrEnum):
    critical = "critical"
    major = "major"
    moderate = "moderate"
    minor = "minor"
    editorial = "editorial"


DbOpt = Annotated[Path, typer.Option("--db", envvar="LABELLEDGER_DB", help="SQLite archive path")]
DEFAULT_DB = Path("labelledger.db")


def _sev(name: SevName) -> Severity:
    return Severity[name.value.upper()]


def _emit(text: str, out: Path | None) -> None:
    if out is None:
        sys.stdout.write(text)
        return
    out.write_text(text, encoding="utf-8")
    typer.echo(f"wrote {out}", err=True)


def _load(path: Path):  # type: ignore[no-untyped-def]
    try:
        return parse_spl_file(path)
    except (OSError, SPLParseError) as exc:
        typer.echo(f"error: {path}: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def parse(path: Path) -> None:
    """Show a label's metadata and section outline."""
    doc = _load(path)
    typer.echo(
        f"{doc.display_title} | set {doc.set_id} | v{doc.version} | {doc.effective_date} | {doc.labeler}"
    )
    typer.echo(
        f"application: {', '.join(doc.application_numbers) or '-'} | format: {doc.format_kind}"
    )
    for s in doc.iter_sections():
        typer.echo(
            f"{'  ' * (s.depth - 1)}- [{s.code or '-'}] {s.heading} ({s.family}, tier {s.tier}, {len(s.lines)} lines)"
        )


@app.command()
def diff(
    old: Path,
    new: Path,
    fmt: Annotated[Fmt, typer.Option("--format", "-f")] = Fmt.md,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    ai: Annotated[
        bool, typer.Option("--ai/--no-ai", help="AI summary if credentials are configured")
    ] = True,
    fail_on: Annotated[
        SevName | None, typer.Option(help="Exit 1 if severity reaches this level")
    ] = None,
) -> None:
    """Redline NEW against OLD."""
    result = diff_documents(_load(old), _load(new))
    summary = summarize(result, mode=None if ai else "off")
    if fmt is Fmt.json:
        _emit(diff_to_json(result, summary), out)
    elif fmt is Fmt.html:
        _emit(render_diff_html(result, summary), out)
    else:
        _emit(diff_to_markdown(result, summary), out)
    if fail_on is not None and result.severity >= _sev(fail_on):
        raise typer.Exit(1)


@app.command()
def drift(
    reference: Path,
    follower: Path,
    fmt: Annotated[Fmt, typer.Option("--format", "-f")] = Fmt.md,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    min_severity: Annotated[SevName, typer.Option("--min")] = SevName.moderate,
    fail_on: Annotated[
        SevName | None, typer.Option(help="Exit 1 if severity reaches this level")
    ] = None,
) -> None:
    """Check how far FOLLOWER (e.g. your generic label) has drifted from REFERENCE."""
    report = compare_labels(_load(reference), _load(follower))
    floor = _sev(min_severity)
    if fmt is Fmt.json:
        _emit(drift_to_json(report), out)
    elif fmt is Fmt.html:
        _emit(render_drift_html(report, min_severity=floor), out)
    else:
        _emit(drift_to_markdown(report, min_severity=floor), out)
    if fail_on is not None and report.severity >= _sev(fail_on):
        raise typer.Exit(1)


@app.command("ingest")
def ingest_cmd(
    paths: list[Path], db: DbOpt = DEFAULT_DB, ai: bool = typer.Option(True, "--ai/--no-ai")
) -> None:
    """Add SPL files to the archive; redlines are recorded for new versions of stored labels."""
    with Store(db) as store:
        _ingest_paths(store, paths, ai=ai)


def _ingest_paths(store: Store, paths: list[Path], *, ai: bool) -> None:
    for path in paths:
        try:
            result = ingest(store, path.read_bytes(), source=f"file:{path.name}", ai=ai)
        except (OSError, SPLParseError) as exc:
            typer.echo(f"skip {path}: {exc}", err=True)
            continue
        state = "stored" if result.created else "already stored"
        extra = (
            f"; change #{result.change_id} ({result.diff.severity.label})" if result.diff else ""
        )
        typer.echo(f"{path.name}: {result.doc.display_title} v{result.doc.version} {state}{extra}")


@app.command()
def fetch(
    set_id: str, db: DbOpt = DEFAULT_DB, ai: bool = typer.Option(True, "--ai/--no-ai")
) -> None:
    """Fetch the current version of a label from DailyMed into the archive."""
    try:
        with Store(db) as store, DailyMedClient() as client:
            result = ingest(store, client.current_xml(set_id), source="dailymed", ai=ai)
    except DailyMedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        f"{result.doc.display_title} v{result.doc.version} {'stored' if result.created else 'already stored'}"
    )


@watch_app.command("add")
def watch_add(
    set_id: str,
    role: Annotated[str, typer.Option(help=f"One of {', '.join(ROLES)}")] = "reference",
    follower: Annotated[
        str | None, typer.Option(help="Set ID of your label that follows this one")
    ] = None,
    note: str = "",
    db: DbOpt = DEFAULT_DB,
) -> None:
    """Watch a label for new versions."""
    if role not in ROLES:
        typer.echo(f"role must be one of {', '.join(ROLES)}", err=True)
        raise typer.Exit(2)
    with Store(db) as store:
        wid = store.add_watch(set_id, role, follower, note)
    typer.echo(f"watch #{wid}: {set_id} as {role}")


@watch_app.command("list")
def watch_list(db: DbOpt = DEFAULT_DB) -> None:
    """List watched labels."""
    with Store(db) as store:
        rows = store.watches()
    for w in rows:
        typer.echo(
            f"#{w['id']} {w['role']:10} {w['set_id']} {w['title'] or ''} (v{w['latest_version'] or '?'})"
        )


@watch_app.command("remove")
def watch_remove(watch_id: int, db: DbOpt = DEFAULT_DB) -> None:
    """Stop watching."""
    with Store(db) as store:
        removed = store.remove_watch(watch_id)
    typer.echo("removed" if removed else "no such watch")


@app.command("poll")
def poll_cmd(db: DbOpt = DEFAULT_DB, ai: bool = typer.Option(True, "--ai/--no-ai")) -> None:
    """Check DailyMed for new versions of every watched label."""
    with Store(db) as store, DailyMedClient() as client:
        outcomes = poll(store, client, ai=ai)
    errors = 0
    for o in outcomes:
        tail = f" change #{o.change_id} ({o.severity})" if o.change_id else ""
        typer.echo(f"{o.set_id}: {o.status} v{o.version or '?'}{tail} {o.message}".rstrip())
        errors += o.status == "error"
    if errors:
        raise typer.Exit(1)


@app.command()
def digest(
    days: int = 7,
    db: DbOpt = DEFAULT_DB,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    base_url: str = "",
) -> None:
    """Markdown digest of recorded changes over the last N days."""
    since = (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat()
    with Store(db) as store:
        entries = build_digest(store, since)
    _emit(digest_markdown(entries, since, base_url), out)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    db: DbOpt = DEFAULT_DB,
) -> None:
    """Run the web app. Binding beyond loopback requires LABELLEDGER_BASIC_AUTH."""
    import uvicorn

    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and not os.environ.get("LABELLEDGER_BASIC_AUTH"):
        typer.echo(
            "refusing to listen on a non-loopback address without LABELLEDGER_BASIC_AUTH=user:password",
            err=True,
        )
        raise typer.Exit(2)
    from .web.app import create_app

    uvicorn.run(
        create_app(str(db)), host=host, port=port, proxy_headers=True, forwarded_allow_ips="*"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
