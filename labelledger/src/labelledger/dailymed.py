"""Client for the NLM DailyMed web services (v2).

DailyMed publishes every SPL submitted to FDA. The endpoints used here:

* ``GET /services/v2/spls.json`` — search SPLs (by drug name, or by published
  date for "what changed since" polling); paginated.
* ``GET /services/v2/spls/{setid}/history.json`` — version history of one label.
* ``GET /services/v2/spls/{setid}.xml`` — the current SPL XML of one label.

DailyMed serves the *current* version of a label through the API. LabelLedger
therefore snapshots every version it observes, building its own version
archive from the day a label is watched — historical versions can be imported
from DailyMed's bulk archives separately.

Politeness and robustness: a minimum interval between requests, timeouts,
bounded retries with exponential backoff that honour ``Retry-After`` on 429 and
retry 5xx, a response-size cap, and an identifying User-Agent (configure a
contact address with ``LABELLEDGER_CONTACT``; none is sent by default).

Note: endpoint behaviour was implemented from DailyMed's published API and
verified against recorded responses in tests; this build environment could
not reach DailyMed, so run ``labelledger fetch`` once against the live service
before relying on it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from . import __version__

BASE_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
MAX_RESPONSE_BYTES = 60 * 1024 * 1024
RETRY_STATUSES = {429, 500, 502, 503, 504}


class DailyMedError(RuntimeError):
    """DailyMed could not be reached or returned an unusable response."""


@dataclass(frozen=True)
class SplSummary:
    set_id: str
    title: str
    version: int | None
    published: date | None


@dataclass(frozen=True)
class SplVersion:
    version: int
    published: date | None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def user_agent() -> str:
    contact = os.environ.get("LABELLEDGER_CONTACT", "").strip()
    suffix = f"; +{contact}" if contact else ""
    return f"LabelLedger/{__version__} (label-change monitoring{suffix})"


class DailyMedClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        min_interval: float = 0.34,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._own = http is None
        self.http = http or httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": user_agent(), "Accept": "application/json, application/xml"},
            follow_redirects=True,
        )
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._sleep = sleep
        self._last = 0.0

    def close(self) -> None:
        if self._own:
            self.http.close()

    def __enter__(self) -> DailyMedClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        attempt = 0
        while True:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.http.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise DailyMedError(f"cannot reach DailyMed: {exc}") from exc
                self._sleep(min(2**attempt, 30))
                attempt += 1
                continue
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = _int(response.headers.get("Retry-After"))
                self._sleep(float(retry_after) if retry_after is not None else min(2**attempt, 30))
                attempt += 1
                continue
            if response.status_code == 404:
                raise DailyMedError(f"not found: {url}")
            if response.status_code >= 400:
                raise DailyMedError(f"DailyMed returned HTTP {response.status_code} for {url}")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise DailyMedError("response exceeds size limit")
            return response

    def _json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._get(path, params)
        try:
            data = response.json()
        except ValueError as exc:
            raise DailyMedError(f"invalid JSON from {path}") from exc
        if not isinstance(data, dict):
            raise DailyMedError(f"unexpected JSON shape from {path}")
        return data

    # -- API -----------------------------------------------------------------

    def search(
        self,
        *,
        drug_name: str | None = None,
        published_since: date | None = None,
        page: int = 1,
        pagesize: int = 100,
    ) -> tuple[list[SplSummary], int | None]:
        """One page of SPL search results and the total page count, if reported."""
        params: dict[str, Any] = {"page": page, "pagesize": pagesize}
        if drug_name:
            params["drug_name"] = drug_name
        if published_since:
            params["published_date"] = published_since.isoformat()
            params["published_date_comparison"] = "gte"
        data = self._json("spls.json", params)
        items = []
        for row in data.get("data", []) or []:
            if not isinstance(row, dict) or not row.get("setid"):
                continue
            items.append(
                SplSummary(
                    set_id=str(row["setid"]),
                    title=str(row.get("title", "")),
                    version=_int(row.get("spl_version")),
                    published=_parse_date(row.get("published_date")),
                )
            )
        meta = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
        return items, _int(meta.get("total_pages"))

    def iter_search(self, **kwargs: Any) -> Iterator[SplSummary]:
        page = 1
        while True:
            items, total = self.search(page=page, **kwargs)
            yield from items
            if not items or (total is not None and page >= total):
                return
            page += 1

    def history(self, set_id: str) -> list[SplVersion]:
        data = self._json(f"spls/{set_id}/history.json")
        versions = []
        for row in data.get("data", []) or []:
            if not isinstance(row, dict):
                continue
            v = _int(row.get("spl_version"))
            if v is not None:
                versions.append(SplVersion(v, _parse_date(row.get("published_date"))))
        return sorted(versions, key=lambda x: x.version)

    def current_xml(self, set_id: str) -> bytes:
        response = self._get(f"spls/{set_id}.xml")
        if b"<document" not in response.content[:4096]:
            raise DailyMedError(f"response for {set_id} is not SPL XML")
        return response.content
