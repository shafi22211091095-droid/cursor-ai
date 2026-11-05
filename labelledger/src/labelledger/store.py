"""SQLite persistence: the label version archive, watchlist, changes and alerts.

One file, no server. Every captured SPL version is stored gzip-compressed with
its SHA-256, so the archive can be audited and any historical redline can be
regenerated exactly. The SQL is deliberately portable (no SQLite-only types
beyond BLOB) so the schema moves to PostgreSQL unchanged when multi-tenant
hosting arrives.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .spl import LabelDocument, parse_spl

SCHEMA_VERSION = 1
ROLES = ("reference", "own", "competitor")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS labels (
    set_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    labeler TEXT,
    doc_type TEXT,
    application_numbers TEXT NOT NULL DEFAULT '[]',
    generic_names TEXT NOT NULL DEFAULT '[]',
    latest_version INTEGER,
    latest_effective TEXT,
    first_seen TEXT NOT NULL,
    last_checked TEXT
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    set_id TEXT NOT NULL REFERENCES labels(set_id),
    version INTEGER NOT NULL,
    document_id TEXT NOT NULL,
    effective_date TEXT,
    sha256 TEXT NOT NULL,
    xml_gz BLOB NOT NULL,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE (set_id, version)
);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY,
    set_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('reference', 'own', 'competitor')),
    follower_set_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (set_id, role, follower_set_id)
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY,
    set_id TEXT NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    severity TEXT NOT NULL,
    counts TEXT NOT NULL,
    report_json TEXT NOT NULL,
    summary_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (set_id, from_version, to_version)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    change_id INTEGER NOT NULL REFERENCES changes(id),
    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed', 'dismissed')),
    created_at TEXT NOT NULL,
    UNIQUE (change_id, watch_id)
);
CREATE INDEX IF NOT EXISTS ix_changes_created ON changes(created_at);
CREATE INDEX IF NOT EXISTS ix_versions_set ON versions(set_id, version);
"""


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class IntegrityError(RuntimeError):
    """Stored XML no longer matches its recorded checksum."""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def migrate(self) -> None:
        with self.tx() as c:
            c.executescript(SCHEMA)
            c.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (str(SCHEMA_VERSION),),
            )

    # -- labels and versions ----------------------------------------------

    def save_version(self, doc: LabelDocument, xml: bytes, source: str) -> bool:
        """Store one SPL version. Returns False if that set/version is already stored."""
        if hashlib.sha256(xml).hexdigest() != doc.sha256:
            raise IntegrityError("document checksum does not match the XML supplied")
        now = utcnow()
        with self.tx() as c:
            existing = c.execute(
                "SELECT sha256 FROM versions WHERE set_id = ? AND version = ?",
                (doc.set_id, doc.version),
            ).fetchone()
            row = c.execute(
                "SELECT latest_version FROM labels WHERE set_id = ?", (doc.set_id,)
            ).fetchone()
            is_latest = (
                row is None or row["latest_version"] is None or doc.version >= row["latest_version"]
            )
            label_fields = (
                doc.display_title,
                doc.labeler,
                doc.doc_type_name,
                json.dumps(list(doc.application_numbers)),
                json.dumps(list(doc.generic_names)),
            )
            if row is None:
                c.execute(
                    "INSERT INTO labels(set_id, title, labeler, doc_type, application_numbers, generic_names,"
                    " latest_version, latest_effective, first_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        doc.set_id,
                        *label_fields,
                        doc.version,
                        doc.effective_date.isoformat() if doc.effective_date else None,
                        now,
                    ),
                )
            elif is_latest:
                c.execute(
                    "UPDATE labels SET title=?, labeler=?, doc_type=?, application_numbers=?, generic_names=?,"
                    " latest_version=?, latest_effective=? WHERE set_id=?",
                    (
                        *label_fields,
                        doc.version,
                        doc.effective_date.isoformat() if doc.effective_date else None,
                        doc.set_id,
                    ),
                )
            if existing is not None:
                return False
            c.execute(
                "INSERT INTO versions(set_id, version, document_id, effective_date, sha256, xml_gz,"
                " captured_at, source) VALUES (?,?,?,?,?,?,?,?)",
                (
                    doc.set_id,
                    doc.version,
                    doc.document_id,
                    doc.effective_date.isoformat() if doc.effective_date else None,
                    doc.sha256,
                    gzip.compress(xml, mtime=0),
                    now,
                    source,
                ),
            )
        return True

    def version_xml(self, set_id: str, version: int) -> bytes:
        row = self.conn.execute(
            "SELECT xml_gz, sha256 FROM versions WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"{set_id} v{version} is not stored")
        xml = gzip.decompress(row["xml_gz"])
        if hashlib.sha256(xml).hexdigest() != row["sha256"]:
            raise IntegrityError(f"{set_id} v{version} failed its checksum")
        return xml

    def document(self, set_id: str, version: int) -> LabelDocument:
        return parse_spl(self.version_xml(set_id, version), source=f"store:{set_id}/v{version}")

    def latest_version(self, set_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(version) AS v FROM versions WHERE set_id = ?", (set_id,)
        ).fetchone()
        return int(row["v"]) if row and row["v"] is not None else None

    def previous_version(self, set_id: str, version: int) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(version) AS v FROM versions WHERE set_id = ? AND version < ?",
            (set_id, version),
        ).fetchone()
        return int(row["v"]) if row and row["v"] is not None else None

    def versions(self, set_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT version, document_id, effective_date, sha256, captured_at, source FROM versions"
            " WHERE set_id = ? ORDER BY version DESC",
            (set_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def label(self, set_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM labels WHERE set_id = ?", (set_id,)).fetchone()
        return self._label_dict(row) if row else None

    def labels(self, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if query:
            like = f"%{query.strip()}%"
            rows = self.conn.execute(
                "SELECT * FROM labels WHERE title LIKE ? OR labeler LIKE ? OR set_id LIKE ?"
                " OR application_numbers LIKE ? OR generic_names LIKE ? ORDER BY title LIMIT ?",
                (like, like, like, like, like, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM labels ORDER BY title LIMIT ?", (limit,)
            ).fetchall()
        return [self._label_dict(r) for r in rows]

    @staticmethod
    def _label_dict(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["application_numbers"] = json.loads(out["application_numbers"])
        out["generic_names"] = json.loads(out["generic_names"])
        return out

    def touch_checked(self, set_id: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE labels SET last_checked = ? WHERE set_id = ?", (utcnow(), set_id))

    # -- watches -------------------------------------------------------------

    def add_watch(
        self, set_id: str, role: str, follower_set_id: str | None = None, note: str = ""
    ) -> int:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self.tx() as c:
            c.execute(
                "INSERT INTO watches(set_id, role, follower_set_id, note, created_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(set_id, role, follower_set_id) DO UPDATE SET note = excluded.note",
                (set_id, role, follower_set_id, note, utcnow()),
            )
            row = c.execute(
                "SELECT id FROM watches WHERE set_id = ? AND role = ? AND follower_set_id IS ?",
                (set_id, role, follower_set_id),
            ).fetchone()
        return int(row["id"])

    def remove_watch(self, watch_id: int) -> bool:
        with self.tx() as c:
            cur = c.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        return cur.rowcount > 0

    def watches(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT w.*, l.title, l.latest_version, l.latest_effective, l.last_checked,"
            " f.title AS follower_title FROM watches w"
            " LEFT JOIN labels l ON l.set_id = w.set_id"
            " LEFT JOIN labels f ON f.set_id = w.follower_set_id ORDER BY w.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def watched_set_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT set_id FROM watches ORDER BY set_id").fetchall()
        return [r["set_id"] for r in rows]

    # -- changes and alerts --------------------------------------------------

    def record_change(
        self,
        set_id: str,
        from_version: int,
        to_version: int,
        severity: str,
        counts: dict[str, int],
        report_json: str,
        summary_json: str | None,
    ) -> int:
        with self.tx() as c:
            c.execute(
                "INSERT INTO changes(set_id, from_version, to_version, severity, counts, report_json,"
                " summary_json, created_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(set_id, from_version, to_version) DO UPDATE SET severity=excluded.severity,"
                " counts=excluded.counts, report_json=excluded.report_json, summary_json=excluded.summary_json",
                (
                    set_id,
                    from_version,
                    to_version,
                    severity,
                    json.dumps(counts),
                    report_json,
                    summary_json,
                    utcnow(),
                ),
            )
            row = c.execute(
                "SELECT id FROM changes WHERE set_id=? AND from_version=? AND to_version=?",
                (set_id, from_version, to_version),
            ).fetchone()
            change_id = int(row["id"])
            for w in c.execute("SELECT id FROM watches WHERE set_id = ?", (set_id,)).fetchall():
                c.execute(
                    "INSERT INTO alerts(change_id, watch_id, created_at) VALUES (?,?,?)"
                    " ON CONFLICT(change_id, watch_id) DO NOTHING",
                    (change_id, w["id"], utcnow()),
                )
        return change_id

    def changes(
        self, set_id: str | None = None, since: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT c.id, c.set_id, c.from_version, c.to_version, c.severity, c.counts, c.created_at,"
            " c.summary_json IS NOT NULL AS has_summary, l.title, l.labeler FROM changes c"
            " LEFT JOIN labels l ON l.set_id = c.set_id WHERE 1=1"
        )
        args: list[Any] = []
        if set_id:
            sql += " AND c.set_id = ?"
            args.append(set_id)
        if since:
            sql += " AND c.created_at >= ?"
            args.append(since)
        sql += " ORDER BY c.created_at DESC, c.id DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            d = dict(r)
            d["counts"] = json.loads(d["counts"])
            out.append(d)
        return out

    def change(self, change_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
        return dict(row) if row else None

    def alerts(self, status: str = "new") -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT a.id, a.status, a.created_at, c.id AS change_id, c.set_id, c.from_version, c.to_version,"
            " c.severity, w.role, w.follower_set_id, l.title FROM alerts a"
            " JOIN changes c ON c.id = a.change_id JOIN watches w ON w.id = a.watch_id"
            " LEFT JOIN labels l ON l.set_id = c.set_id WHERE a.status = ? ORDER BY a.created_at DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_alert_status(self, alert_id: int, status: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE alerts SET status = ? WHERE id = ?", (status, alert_id))
