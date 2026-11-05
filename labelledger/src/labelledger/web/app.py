"""LabelLedger web application (FastAPI, server-rendered, no JavaScript).

Security posture for a private beta:

* Optional HTTP Basic authentication over every route except ``/healthz``
  (``LABELLEDGER_BASIC_AUTH=user:password``). Without it the server refuses to
  bind anywhere but loopback (enforced in ``cli.serve``).
* CSRF protection on every state-changing form: a random token in a
  ``SameSite=Strict`` cookie must match a hidden form field.
* A strict Content-Security-Policy (no scripts at all), ``nosniff``,
  ``frame-ancestors 'none'`` and no-referrer.
* Uploads are size-capped and parsed by the hardened SPL parser (no DTDs, no
  entities, no network). Stored XML is served only as a download.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .. import __version__
from ..ai import SummaryResult, summarize
from ..dailymed import DailyMedClient
from ..diff import diff_documents
from ..digest import build_digest, digest_markdown
from ..drift import compare_labels
from ..report import (
    diff_to_json,
    diff_to_markdown,
    drift_to_json,
    drift_to_markdown,
    render_diff_html,
    render_drift_html,
)
from ..severity import Severity
from ..spl import MAX_BYTES, SPLParseError, parse_spl
from ..store import ROLES, Store
from ..watch import ingest, poll

HERE = Path(__file__).parent
CSRF_COOKIE = "ll_csrf"
CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
SEVERITY_BY_NAME = {s.label: s for s in Severity}


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return response


class BasicAuth(BaseHTTPMiddleware):
    def __init__(self, app: Any, credentials: str) -> None:
        super().__init__(app)
        user, _, password = credentials.partition(":")
        if not user or not password:
            raise ValueError("LABELLEDGER_BASIC_AUTH must look like user:password")
        self.user, self.password = user.encode(), password.encode()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/healthz":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:], validate=True).partition(b":")
            except (binascii.Error, ValueError):
                user, pw = b"", b""
            if secrets.compare_digest(user, self.user) and secrets.compare_digest(
                pw, self.password
            ):
                return await call_next(request)
        return PlainTextResponse(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="LabelLedger"'},
        )


def _severity(name: str | None, default: Severity) -> Severity:
    return SEVERITY_BY_NAME.get((name or "").lower(), default)


async def _read_upload(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"{upload.filename}: file exceeds {MAX_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(400, f"{upload.filename or 'upload'}: empty file")
    return data


def create_app(db_path: str | None = None, *, basic_auth: str | None = None) -> FastAPI:
    auth = basic_auth if basic_auth is not None else os.environ.get("LABELLEDGER_BASIC_AUTH")
    if auth:
        user, _, password = auth.partition(":")
        if not user or not password:  # fail at startup, not on the first request
            raise ValueError("LABELLEDGER_BASIC_AUTH must look like user:password")
    store = Store(db_path or os.environ.get("LABELLEDGER_DB", "labelledger.db"))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        store.close()

    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals["engine_version"] = __version__
    app = FastAPI(
        lifespan=lifespan,
        title="LabelLedger",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.add_middleware(SecurityHeaders)
    if auth:
        app.add_middleware(BasicAuth, credentials=auth)

    def page(request: Request, name: str, status: int = 200, **ctx: Any) -> HTMLResponse:
        token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        response = templates.TemplateResponse(
            request, name, {"csrf": token, **ctx}, status_code=status
        )
        if request.cookies.get(CSRF_COOKIE) != token:
            response.set_cookie(
                CSRF_COOKIE,
                token,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
            )
        return response

    def check_csrf(request: Request, token: str) -> None:
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if not cookie or not token or not secrets.compare_digest(cookie, token):
            raise HTTPException(
                403, "Form expired or forged (CSRF check failed). Reload the page and try again."
            )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return page(
            request,
            "message.html",
            status=exc.status_code,
            title="Something went wrong",
            message=str(exc.detail),
        )

    # -- pages ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return page(
            request,
            "index.html",
            changes=store.changes(limit=15),
            watches=store.watches(),
            alerts=store.alerts("new"),
            labels=store.labels(limit=500),
            roles=ROLES,
        )

    @app.get("/labels", response_class=HTMLResponse)
    def labels(request: Request, q: str | None = None) -> HTMLResponse:
        return page(request, "labels.html", labels=store.labels(q), q=q or "")

    @app.get("/labels/{set_id}", response_class=HTMLResponse)
    def label(request: Request, set_id: str) -> HTMLResponse:
        meta = store.label(set_id)
        if meta is None:
            raise HTTPException(
                404, "Label not found in your archive. Upload it or add it to a watchlist first."
            )
        return page(
            request,
            "label.html",
            label=meta,
            versions=store.versions(set_id),
            changes=store.changes(set_id=set_id),
            watches=[
                w
                for w in store.watches()
                if w["set_id"] == set_id or w["follower_set_id"] == set_id
            ],
            others=[x for x in store.labels(limit=500) if x["set_id"] != set_id],
            roles=ROLES,
        )

    @app.get("/labels/{set_id}/versions/{version}.xml")
    def version_xml(set_id: str, version: int) -> Response:
        try:
            xml = store.version_xml(set_id, version)
        except KeyError as exc:
            raise HTTPException(404, "Version not stored") from exc
        return Response(
            xml,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{set_id}_v{version}.xml"'},
        )

    def _stored_change(change_id: int) -> tuple[Any, SummaryResult | None]:
        row = store.change(change_id)
        if row is None:
            raise HTTPException(404, "Change not found")
        diff = diff_documents(
            store.document(row["set_id"], row["from_version"]),
            store.document(row["set_id"], row["to_version"]),
        )
        summary = None
        if row["summary_json"]:
            summary = SummaryResult.from_dict(json.loads(row["summary_json"]).get("summary", {}))
        return diff, summary

    @app.get("/changes/{change_id}", response_class=HTMLResponse)
    def change_page(request: Request, change_id: int) -> HTMLResponse:
        diff, summary = _stored_change(change_id)
        return page(request, "page_diff.html", diff=diff, summary=summary, change_id=change_id)

    @app.get("/changes/{change_id}/export")
    def change_export(change_id: int, format: str = "html") -> Response:
        diff, summary = _stored_change(change_id)
        return _export_diff(
            diff,
            summary,
            format,
            f"redline_{diff.new.set_id}_v{diff.old.version}-v{diff.new.version}",
        )

    @app.get("/diff", response_class=HTMLResponse)
    def diff_page(
        request: Request, set_id: str, v_from: int, v_to: int, format: str | None = None
    ) -> Response:
        try:
            diff = diff_documents(store.document(set_id, v_from), store.document(set_id, v_to))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        summary = summarize(diff, mode="off")
        if format:
            return _export_diff(diff, summary, format, f"redline_{set_id}_v{v_from}-v{v_to}")
        return page(request, "page_diff.html", diff=diff, summary=summary, change_id=None)

    @app.get("/drift", response_class=HTMLResponse)
    def drift_page(
        request: Request,
        reference: str,
        follower: str,
        min: str = "moderate",
        format: str | None = None,
    ) -> Response:
        rv, fv = store.latest_version(reference), store.latest_version(follower)
        if rv is None or fv is None:
            raise HTTPException(404, "Both labels must be in your archive")
        report = compare_labels(store.document(reference, rv), store.document(follower, fv))
        floor = _severity(min, Severity.MODERATE)
        if format:
            return _export_drift(report, format, floor, f"drift_{follower}_vs_{reference}")
        hidden = sum(1 for f in report.findings if f.severity < floor)
        return page(
            request,
            "page_drift.html",
            report=report,
            min_severity=int(floor),
            hidden_count=hidden,
            min_name=floor.label,
        )

    @app.get("/digest", response_class=PlainTextResponse)
    def digest(days: int = 7) -> PlainTextResponse:
        since = (
            (datetime.now(UTC) - timedelta(days=max(1, min(days, 90))))
            .replace(microsecond=0)
            .isoformat()
        )
        return PlainTextResponse(
            digest_markdown(build_digest(store, since), since), media_type="text/markdown"
        )

    # -- actions -------------------------------------------------------------

    @app.post("/upload")
    async def upload(
        request: Request, csrf: Annotated[str, Form()], files: Annotated[list[UploadFile], File()]
    ) -> Response:
        check_csrf(request, csrf)
        results = []
        for f in files:
            data = await _read_upload(f)
            try:
                res = ingest(store, data, source=f"upload:{f.filename}")
            except SPLParseError as exc:
                raise HTTPException(422, f"{f.filename}: {exc}") from exc
            results.append(res)
        if len(results) == 1:
            r = results[0]
            target = f"/changes/{r.change_id}" if r.change_id else f"/labels/{r.doc.set_id}"
            return RedirectResponse(target, status_code=303)
        return page(
            request,
            "message.html",
            title="Upload complete",
            message=f"Imported {len(results)} file(s).",
            links=[
                (f"/labels/{r.doc.set_id}", f"{r.doc.display_title} v{r.doc.version}")
                for r in results
            ],
        )

    @app.post("/compare", response_class=HTMLResponse)
    async def compare(
        request: Request,
        csrf: Annotated[str, Form()],
        first: Annotated[UploadFile, File()],
        second: Annotated[UploadFile, File()],
        mode: Annotated[str, Form()] = "diff",
    ) -> HTMLResponse:
        """Ad-hoc comparison of two uploaded files; nothing is stored."""
        check_csrf(request, csrf)
        try:
            a = parse_spl(await _read_upload(first), source=first.filename or "first")
            b = parse_spl(await _read_upload(second), source=second.filename or "second")
        except SPLParseError as exc:
            raise HTTPException(422, str(exc)) from exc
        if mode == "drift":
            report = compare_labels(a, b)
            hidden = sum(1 for f in report.findings if f.severity < Severity.MODERATE)
            return page(
                request,
                "page_drift.html",
                report=report,
                min_severity=int(Severity.MODERATE),
                hidden_count=hidden,
                min_name="moderate",
                adhoc=True,
            )
        old, new = (a, b) if (a.set_id != b.set_id or a.version <= b.version) else (b, a)
        diff = diff_documents(old, new)
        return page(
            request,
            "page_diff.html",
            diff=diff,
            summary=summarize(diff),
            change_id=None,
            adhoc=True,
        )

    @app.post("/watches")
    def add_watch(
        request: Request,
        csrf: Annotated[str, Form()],
        set_id: Annotated[str, Form()],
        role: Annotated[str, Form()],
        follower_set_id: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ) -> Response:
        check_csrf(request, csrf)
        if role not in ROLES:
            raise HTTPException(400, "Unknown role")
        store.add_watch(set_id.strip(), role, follower_set_id.strip() or None, note.strip()[:200])
        return RedirectResponse(
            f"/labels/{set_id.strip()}" if store.label(set_id.strip()) else "/", status_code=303
        )

    @app.post("/watches/{watch_id}/delete")
    def delete_watch(request: Request, watch_id: int, csrf: Annotated[str, Form()]) -> Response:
        check_csrf(request, csrf)
        store.remove_watch(watch_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/alerts/{alert_id}")
    def update_alert(
        request: Request,
        alert_id: int,
        csrf: Annotated[str, Form()],
        status: Annotated[str, Form()],
    ) -> Response:
        check_csrf(request, csrf)
        if status not in ("reviewed", "dismissed", "new"):
            raise HTTPException(400, "Unknown status")
        store.set_alert_status(alert_id, status)
        return RedirectResponse("/", status_code=303)

    @app.post("/poll", response_class=HTMLResponse)
    def run_poll(request: Request, csrf: Annotated[str, Form()]) -> HTMLResponse:
        check_csrf(request, csrf)
        with DailyMedClient() as client:
            outcomes = poll(store, client)
        return page(
            request,
            "message.html",
            title="DailyMed check complete",
            message=f"Checked {len(outcomes)} watched label(s).",
            outcomes=outcomes,
        )

    # -- JSON API --------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/labels")
    def api_labels(q: str | None = None) -> list[dict[str, Any]]:
        return store.labels(q)

    @app.get("/api/labels/{set_id}")
    def api_label(set_id: str) -> dict[str, Any]:
        meta = store.label(set_id)
        if meta is None:
            raise HTTPException(404, "not found")
        return {**meta, "versions": store.versions(set_id), "changes": store.changes(set_id=set_id)}

    @app.get("/api/changes")
    def api_changes(since: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return store.changes(since=since, limit=max(1, min(limit, 1000)))

    @app.get("/api/changes/{change_id}")
    def api_change(change_id: int) -> Response:
        row = store.change(change_id)
        if row is None:
            raise HTTPException(404, "not found")
        return Response(row["summary_json"] or row["report_json"], media_type="application/json")

    return app


def _download(body: str, media_type: str, filename: str) -> Response:
    return Response(
        body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export_diff(diff: Any, summary: SummaryResult | None, fmt: str, stem: str) -> Response:
    if fmt == "json":
        return _download(diff_to_json(diff, summary), "application/json", f"{stem}.json")
    if fmt in ("md", "markdown"):
        return _download(diff_to_markdown(diff, summary), "text/markdown", f"{stem}.md")
    if fmt == "html":
        return _download(render_diff_html(diff, summary), "text/html", f"{stem}.html")
    raise HTTPException(400, "format must be html, md or json")


def _export_drift(report: Any, fmt: str, floor: Severity, stem: str) -> Response:
    if fmt == "json":
        return _download(drift_to_json(report), "application/json", f"{stem}.json")
    if fmt in ("md", "markdown"):
        return _download(
            drift_to_markdown(report, min_severity=floor), "text/markdown", f"{stem}.md"
        )
    if fmt == "html":
        return _download(render_drift_html(report, min_severity=floor), "text/html", f"{stem}.html")
    raise HTTPException(400, "format must be html, md or json")
