from __future__ import annotations

import base64
import json
import re

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from labelledger.cli import app as cli_app
from labelledger.web.app import create_app

from .conftest import WARFARIN_2012, WARFARIN_2026

SET_ID = "1244c338-9140-425a-bf2a-9da6b4ac0287"
XML = "application/xml"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("LABELLEDGER_BASIC_AUTH", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(create_app(str(tmp_path / "web.db"))) as c:
        yield c


def csrf(client):
    html = client.get("/").text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_health_headers_and_dashboard(client):
    r = client.get("/healthz")
    assert r.json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200 and "Compare two labels now" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert (
        page.headers["x-frame-options"] == "DENY"
        and page.headers["x-content-type-options"] == "nosniff"
    )
    assert "samesite=strict" in page.headers["set-cookie"].lower()


def test_forms_require_csrf(client):
    files = {"files": ("a.xml", WARFARIN_2026.read_bytes(), XML)}
    r = client.post("/upload", data={"csrf": "forged"}, files=files)
    assert r.status_code == 403 and "CSRF" in r.text


def test_upload_new_version_then_view_and_export(client, warfarin_v21_bytes):
    token = csrf(client)
    r = client.post(
        "/upload",
        data={"csrf": token},
        files={"files": ("v20.xml", WARFARIN_2026.read_bytes(), XML)},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == f"/labels/{SET_ID}"
    r = client.post(
        "/upload",
        data={"csrf": token},
        files={"files": ("v21.xml", warfarin_v21_bytes, XML)},
        follow_redirects=False,
    )
    assert r.headers["location"].startswith("/changes/")
    change_url = r.headers["location"]
    page = client.get(change_url)
    assert (
        page.status_code == 200
        and "Section-by-section redline" in page.text
        and "CRITICAL" in page.text.upper()
    )
    exported = client.get(f"{change_url}/export?format=json")
    assert exported.headers["content-disposition"].startswith("attachment")
    assert json.loads(exported.text)["severity"] == "critical"
    assert "# Redline" in client.get(f"{change_url}/export?format=md").text
    assert "<style>" in client.get(f"{change_url}/export?format=html").text
    assert client.get(f"{change_url}/export?format=pdf").status_code == 400
    label = client.get(f"/labels/{SET_ID}")
    assert "v21" in label.text and "Recorded changes" in label.text
    xml = client.get(f"/labels/{SET_ID}/versions/21.xml")
    assert xml.headers["content-disposition"].startswith("attachment")
    assert client.get(f"/diff?set_id={SET_ID}&v_from=20&v_to=21").status_code == 200
    assert client.get(f"/diff?set_id={SET_ID}&v_from=20&v_to=21&format=md").text.startswith(
        "# Redline"
    )
    assert client.get("/api/changes").json()[0]["severity"] == "critical"
    assert client.get(f"/api/labels/{SET_ID}").json()["versions"][0]["version"] == 21
    assert "[CRITICAL]" in client.get("/digest").text
    assert client.get("/labels?q=warfarin").status_code == 200


def test_drift_between_archived_labels(client):
    token = csrf(client)
    for path in (WARFARIN_2026, WARFARIN_2012):
        client.post(
            "/upload", data={"csrf": token}, files={"files": (path.name, path.read_bytes(), XML)}
        )
    ref, fol = SET_ID, "ed1d5b87-84d2-4ec7-b1bc-cf8f5096e938"
    page = client.get(f"/drift?reference={ref}&follower={fol}&min=major")
    assert (
        page.status_code == 200
        and "Parity by section family" in page.text
        and "calciphylaxis" in page.text.lower()
    )
    assert client.get(f"/drift?reference={ref}&follower={fol}&format=json").json()["parity"] < 1
    assert client.get(f"/drift?reference={ref}&follower=nope").status_code == 404


def test_adhoc_compare_diff_and_drift(client, warfarin_v21_bytes):
    token = csrf(client)
    files = {
        "first": ("a.xml", WARFARIN_2026.read_bytes(), XML),
        "second": ("b.xml", warfarin_v21_bytes, XML),
    }
    r = client.post("/compare", data={"csrf": token, "mode": "diff"}, files=files)
    assert (
        r.status_code == 200
        and "ad-hoc comparison" in r.text
        and "5.10 Serious Hypersensitivity Reactions" in r.text
    )
    r = client.post("/compare", data={"csrf": token, "mode": "drift"}, files=files)
    assert r.status_code == 200 and "Label drift" in r.text
    bad = {"first": ("a.xml", b"<html/>", XML), "second": ("b.xml", b"<html/>", XML)}
    assert client.post("/compare", data={"csrf": token}, files=bad).status_code == 422
    empty = {"first": ("a.xml", b"", XML), "second": ("b.xml", b"", XML)}
    assert client.post("/compare", data={"csrf": token}, files=empty).status_code == 400


def test_label_text_is_html_escaped(client):
    hostile = WARFARIN_2026.read_text(encoding="utf-8").replace(
        "is usually 2 to 5 mg once daily",
        "is usually &lt;script&gt;alert(1)&lt;/script&gt; daily",
        1,
    )
    token = csrf(client)
    files = {
        "first": ("a.xml", WARFARIN_2026.read_bytes(), XML),
        "second": ("b.xml", hostile.encode(), XML),
    }
    r = client.post("/compare", data={"csrf": token, "mode": "diff"}, files=files)
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


def test_watches_and_alert_status(client):
    token = csrf(client)
    r = client.post(
        "/watches",
        data={"csrf": token, "set_id": SET_ID, "role": "reference"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert SET_ID in client.get("/").text
    assert (
        client.post("/watches", data={"csrf": token, "set_id": SET_ID, "role": "boss"}).status_code
        == 400
    )
    assert client.post("/alerts/1", data={"csrf": token, "status": "weird"}).status_code == 400
    assert (
        client.post("/watches/1/delete", data={"csrf": token}, follow_redirects=False).status_code
        == 303
    )


def test_unknown_pages_and_api_errors(client):
    assert client.get("/labels/nope").status_code == 404
    assert client.get("/changes/999").status_code == 404
    assert client.get("/api/changes/999").json() == {"error": "not found"}
    assert client.get("/api/labels/zzz").status_code == 404


def test_basic_auth(tmp_path):
    with TestClient(create_app(str(tmp_path / "a.db"), basic_auth="rx:s3cret")) as c:
        assert c.get("/").status_code == 401
        assert c.get("/healthz").status_code == 200
        good = base64.b64encode(b"rx:s3cret").decode()
        bad = base64.b64encode(b"rx:nope").decode()
        assert c.get("/", headers={"Authorization": f"Basic {bad}"}).status_code == 401
        assert c.get("/", headers={"Authorization": "Basic !!!"}).status_code == 401
        assert c.get("/", headers={"Authorization": f"Basic {good}"}).status_code == 200
    with pytest.raises(ValueError):
        create_app(str(tmp_path / "b.db"), basic_auth="nopassword")


# --- CLI ---------------------------------------------------------------------

runner = CliRunner()


def test_cli_diff_drift_parse_and_fail_on(tmp_path, warfarin_v21_bytes):
    v21 = tmp_path / "v21.xml"
    v21.write_bytes(warfarin_v21_bytes)
    r = runner.invoke(cli_app, ["diff", str(WARFARIN_2026), str(v21), "--no-ai"])
    assert r.exit_code == 0 and "[CRITICAL]" in r.stdout
    r = runner.invoke(
        cli_app, ["diff", str(WARFARIN_2026), str(v21), "--no-ai", "--fail-on", "major"]
    )
    assert r.exit_code == 1
    out = tmp_path / "r.html"
    r = runner.invoke(
        cli_app, ["diff", str(WARFARIN_2026), str(v21), "-f", "html", "-o", str(out), "--no-ai"]
    )
    assert r.exit_code == 0 and out.read_text().startswith("<!doctype html>")
    r = runner.invoke(cli_app, ["drift", str(WARFARIN_2026), str(WARFARIN_2012), "-f", "json"])
    assert r.exit_code == 0 and json.loads(r.stdout)["parity"] < 1
    r = runner.invoke(
        cli_app,
        [
            "drift",
            str(WARFARIN_2026),
            str(WARFARIN_2012),
            "--fail-on",
            "major",
            "-f",
            "html",
            "-o",
            str(tmp_path / "d.html"),
        ],
    )
    assert r.exit_code == 1
    r = runner.invoke(cli_app, ["parse", str(WARFARIN_2026)])
    assert r.exit_code == 0 and "5.4 Acute Kidney Injury" in r.stdout
    r = runner.invoke(cli_app, ["diff", str(tmp_path / "missing.xml"), str(v21)])
    assert r.exit_code == 2
    assert runner.invoke(cli_app, ["version"]).stdout.strip()


def test_cli_archive_watch_digest(tmp_path, warfarin_v21_bytes):
    db = str(tmp_path / "c.db")
    v21 = tmp_path / "v21.xml"
    v21.write_bytes(warfarin_v21_bytes)
    junk = tmp_path / "junk.xml"
    junk.write_text("<html/>")
    r = runner.invoke(
        cli_app, ["ingest", str(WARFARIN_2026), str(v21), str(junk), "--db", db, "--no-ai"]
    )
    assert r.exit_code == 0 and "change #1 (critical)" in r.stdout and "skip" in r.output
    assert "watch #1" in runner.invoke(cli_app, ["watch", "add", SET_ID, "--db", db]).stdout
    assert SET_ID in runner.invoke(cli_app, ["watch", "list", "--db", db]).stdout
    assert (
        runner.invoke(cli_app, ["watch", "add", SET_ID, "--role", "x", "--db", db]).exit_code == 2
    )
    assert "removed" in runner.invoke(cli_app, ["watch", "remove", "1", "--db", db]).stdout
    assert "[CRITICAL]" in runner.invoke(cli_app, ["digest", "--db", db]).stdout


def test_cli_serve_refuses_public_bind_without_auth(monkeypatch):
    monkeypatch.delenv("LABELLEDGER_BASIC_AUTH", raising=False)
    r = runner.invoke(cli_app, ["serve", "--host", "0.0.0.0"])
    assert r.exit_code == 2 and "refusing" in r.output
