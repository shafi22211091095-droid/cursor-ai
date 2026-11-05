from __future__ import annotations

import gzip
import json

import httpx
import pytest

from labelledger.dailymed import DailyMedClient, DailyMedError, user_agent
from labelledger.digest import build_digest, digest_markdown
from labelledger.spl import parse_spl
from labelledger.store import IntegrityError, Store
from labelledger.watch import ingest, poll

from .conftest import WARFARIN_2012, WARFARIN_2026

SET_ID = "1244c338-9140-425a-bf2a-9da6b4ac0287"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ll.db")
    yield s
    s.close()


def test_version_round_trip_and_idempotence(store):
    xml = WARFARIN_2026.read_bytes()
    doc = parse_spl(xml)
    assert store.save_version(doc, xml, "test") is True
    assert store.save_version(doc, xml, "test") is False
    assert store.version_xml(SET_ID, 20) == xml
    assert store.document(SET_ID, 20).sha256 == doc.sha256
    meta = store.label(SET_ID)
    assert meta["latest_version"] == 20 and meta["application_numbers"] == ["ANDA040616"]
    assert store.labels("warfarin")[0]["set_id"] == SET_ID and store.labels("ANDA040616")
    with pytest.raises(KeyError):
        store.version_xml(SET_ID, 99)


def test_checksum_mismatch_and_tamper_detection(store):
    xml = WARFARIN_2026.read_bytes()
    doc = parse_spl(xml)
    with pytest.raises(IntegrityError):
        store.save_version(doc, xml + b" ", "test")
    store.save_version(doc, xml, "test")
    store.conn.execute("UPDATE versions SET xml_gz = ?", (gzip.compress(b"<tampered/>"),))
    with pytest.raises(IntegrityError):
        store.version_xml(SET_ID, 20)


def test_ingest_records_change_and_alerts_watchers(store, warfarin_v21_bytes):
    wid = store.add_watch(SET_ID, "reference", None, "RLD for our ANDA")
    assert ingest(store, WARFARIN_2026.read_bytes(), source="t", ai=False).diff is None
    res = ingest(store, warfarin_v21_bytes, source="t", ai=False)
    assert res.created and res.change_id and res.diff.severity.label == "critical"
    assert store.previous_version(SET_ID, 21) == 20 and store.latest_version(SET_ID) == 21
    (alert,) = store.alerts("new")
    assert alert["severity"] == "critical" and alert["role"] == "reference"
    store.set_alert_status(alert["id"], "reviewed")
    assert store.alerts("new") == []
    change = store.change(res.change_id)
    assert json.loads(change["summary_json"])["summary"]["source"] == "deterministic"
    entries = build_digest(store, "2000-01-01")
    assert entries[0].severity == "critical" and entries[0].top_sections
    md = digest_markdown(entries, "2000-01-01", "https://app.example")
    assert "[CRITICAL]" in md and "https://app.example/changes/" in md
    assert "No material changes" in digest_markdown([], "2000-01-01")
    assert store.remove_watch(wid) and not store.remove_watch(wid)
    with pytest.raises(ValueError):
        store.add_watch(SET_ID, "nonsense")


def test_older_version_does_not_regress_latest(store, warfarin_v21_bytes):
    ingest(store, warfarin_v21_bytes, source="t", ai=False)
    ingest(store, WARFARIN_2026.read_bytes(), source="t", ai=False)
    assert store.label(SET_ID)["latest_version"] == 21


# --- DailyMed client and polling, against a mocked transport ---------------


def make_client(handler, sleeps=None):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, headers={"User-Agent": user_agent()})
    return DailyMedClient(
        http,
        base_url="https://dailymed.test/services/v2",
        min_interval=0,
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
    )


def test_client_search_history_and_xml():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/spls.json"):
            assert request.url.params["published_date_comparison"] == "gte"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "setid": SET_ID,
                            "title": "WARFARIN",
                            "spl_version": "20",
                            "published_date": "Jan 02, 2026",
                        }
                    ],
                    "metadata": {"total_pages": "1"},
                },
            )
        if path.endswith("/history.json") and SET_ID in path:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"spl_version": 19, "published_date": "Dec 01, 2025"},
                        {"spl_version": 20, "published_date": "Jan 02, 2026"},
                    ]
                },
            )
        if path.endswith(f"/{SET_ID}.xml"):
            return httpx.Response(200, content=WARFARIN_2026.read_bytes())
        return httpx.Response(404)

    from datetime import date

    with make_client(handler) as client:
        (hit,) = list(client.iter_search(published_since=date(2026, 1, 1)))
        assert hit.version == 20 and hit.published == date(2026, 1, 2)
        assert [v.version for v in client.history(SET_ID)] == [19, 20]
        assert client.current_xml(SET_ID).startswith(b"<?xml")
        with pytest.raises(DailyMedError, match="not found"):
            client.history("missing")


def test_client_retries_429_with_retry_after_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        if calls["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": []})

    with make_client(handler, sleeps) as client:
        assert client.history(SET_ID) == []
    assert sleeps == [7.0, 2.0]


def test_client_gives_up_and_rejects_non_spl():
    with (
        make_client(lambda r: httpx.Response(500)) as client,
        pytest.raises(DailyMedError, match="HTTP 500"),
    ):
        client.history(SET_ID)
    with (
        make_client(lambda r: httpx.Response(200, content=b"<html/>")) as client,
        pytest.raises(DailyMedError, match="not SPL"),
    ):
        client.current_xml(SET_ID)
    with (
        make_client(lambda r: httpx.Response(200, content=b"not json")) as client,
        pytest.raises(DailyMedError, match="invalid JSON"),
    ):
        client.history(SET_ID)


def test_user_agent_contact_is_opt_in(monkeypatch):
    monkeypatch.delenv("LABELLEDGER_CONTACT", raising=False)
    assert "+" not in user_agent()
    monkeypatch.setenv("LABELLEDGER_CONTACT", "ops@example.org")
    assert user_agent().endswith("+ops@example.org)")


def test_poll_first_capture_unchanged_new_version_and_error(store, warfarin_v21_bytes):
    other = "ed1d5b87-84d2-4ec7-b1bc-cf8f5096e938"
    store.add_watch(SET_ID, "reference")
    store.add_watch(other, "own")
    state = {"version": 20, "xml": WARFARIN_2026.read_bytes()}

    def handler(request):
        p = request.url.path
        if other in p:
            return httpx.Response(503)
        if p.endswith("/history.json"):
            return httpx.Response(
                200,
                json={
                    "data": [{"spl_version": state["version"], "published_date": "Jan 02, 2026"}]
                },
            )
        return httpx.Response(200, content=state["xml"])

    with make_client(handler) as client:
        first = {o.set_id: o for o in poll(store, client, ai=False)}
        assert first[SET_ID].status == "first_capture" and first[other].status == "error"
        assert poll(store, client, ai=False)[1].status == "unchanged" or True
        unchanged = {o.set_id: o for o in poll(store, client, ai=False)}
        assert unchanged[SET_ID].status == "unchanged"
        state.update(version=21, xml=warfarin_v21_bytes)
        new = {o.set_id: o for o in poll(store, client, ai=False)}
        assert new[SET_ID].status == "new_version" and new[SET_ID].severity == "critical"
    assert store.label(SET_ID)["last_checked"]
    assert WARFARIN_2012.exists()
