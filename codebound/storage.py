"""SQLite persistence. All script queries include the authorized tenant predicate."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ScriptStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scripts (
                    script_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 1)
                );
                CREATE INDEX IF NOT EXISTS scripts_by_tenant ON scripts(tenant_id);
                CREATE TABLE IF NOT EXISTS execution_audit (
                    execution_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    script_id TEXT NOT NULL,
                    script_version INTEGER,
                    operation TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(execution_audit)")}
            if "execution_mode" not in columns:
                db.execute("ALTER TABLE execution_audit ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'mock'")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, tenant_id: str, name: str, source_code: str) -> dict:
        now = timestamp()
        record = dict(script_id=str(uuid4()), tenant_id=tenant_id, name=name,
                      source_code=source_code, created_at=now, modified_at=now, version=1)
        with self.connection() as db:
            db.execute("INSERT INTO scripts VALUES (:script_id,:tenant_id,:name,:source_code,"
                       ":created_at,:modified_at,:version)", record)
        return record

    def list(self, tenant_id: str, limit: int, offset: int) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM scripts WHERE tenant_id=? ORDER BY created_at,script_id LIMIT ? OFFSET ?",
                (tenant_id, limit, offset))]

    def get(self, tenant_id: str, script_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM scripts WHERE tenant_id=? AND script_id=?",
                             (tenant_id, script_id)).fetchone()
            return dict(row) if row else None

    def update(self, tenant_id: str, script_id: str, name: str, source_code: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "UPDATE scripts SET name=?,source_code=?,modified_at=?,version=version+1 "
                "WHERE tenant_id=? AND script_id=? RETURNING *",
                (name, source_code, timestamp(), tenant_id, script_id)).fetchone()
            return dict(row) if row else None

    def delete(self, tenant_id: str, script_id: str) -> bool:
        with self.connection() as db:
            return db.execute("DELETE FROM scripts WHERE tenant_id=? AND script_id=?",
                              (tenant_id, script_id)).rowcount == 1

    def audit(self, *, execution_id: str, tenant_id: str, script_id: str,
              script_version: int | None, operation: str, status: str, duration_ms: float,
              execution_mode: str = "mock") -> None:
        # No source, parameters, reference, token, result rows or exception text persisted here.
        with self.connection() as db:
            db.execute("INSERT INTO execution_audit (execution_id,tenant_id,script_id,script_version,operation,"
                       "timestamp,status,duration_ms,execution_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                       (execution_id, tenant_id, script_id, script_version, operation,
                        timestamp(), status, duration_ms, execution_mode))
