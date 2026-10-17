"""AI metadata/reuse and audit share existing SQLite; source is never evaluated here."""

import hashlib
import json
from uuid import uuid4
from codebound.storage import timestamp
from codebound.llm.client import AIError


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def signature(task):
    # Deliberately conservative: no semantic matching, number or punctuation removal.
    return task.strip()


class AIStore:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ai_scripts (
                    script_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, version INTEGER NOT NULL,
                    task_text TEXT NOT NULL, task_signature TEXT NOT NULL, reference_json TEXT NOT NULL,
                    schema_hash TEXT NOT NULL, parameters_json TEXT NOT NULL, source_hash TEXT NOT NULL,
                    model TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ai_by_tenant ON ai_scripts(tenant_id,task_signature);
                CREATE TABLE IF NOT EXISTS ai_events (
                    event_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, operation TEXT NOT NULL,
                    script_id TEXT, status TEXT NOT NULL, timestamp TEXT NOT NULL, metrics_json TEXT NOT NULL
                );
            """)

    def context(self, tenant, script_id):
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM ai_scripts WHERE tenant_id=? AND script_id=?", (tenant, script_id)).fetchone()
            return dict(row) if row else None

    def reusable(self, tenant, task, reference, schema, parameters):
        with self.store.connection() as db:
            rows = db.execute("SELECT scripts.* FROM ai_scripts a JOIN scripts ON scripts.script_id=a.script_id "
                "AND scripts.tenant_id=a.tenant_id AND scripts.version=a.version WHERE a.tenant_id=? "
                "AND a.task_signature=? AND a.reference_json=? AND a.schema_hash=? AND a.parameters_json=? "
                "AND a.source_hash != '' AND EXISTS (SELECT 1 FROM execution_audit e WHERE "
                "e.tenant_id=a.tenant_id AND e.script_id=a.script_id AND e.script_version=a.version "
                "AND e.status='succeeded' AND e.execution_mode='local_docker')", (tenant, signature(task), json.dumps(reference, sort_keys=True),
                    fingerprint(schema), json.dumps(parameters, sort_keys=True))).fetchall()
        # No arbitrary choice when multiple generated candidates match.
        if len(rows) == 1:
            record = dict(rows[0])
            context = self.context(tenant, record["script_id"])
            if context["source_hash"] == fingerprint(record["source_code"]):
                return record
        return None

    def saved(self, tenant, task, reference, schema, parameters, source, model, metrics, *, previous=None):
        now = timestamp()
        with self.store.connection() as db:
            if previous:
                row = db.execute("UPDATE scripts SET source_code=?, modified_at=?, version=version+1 "
                    "WHERE tenant_id=? AND script_id=? AND version=? RETURNING *",
                    (source, now, tenant, previous["script_id"], previous["version"])).fetchone()
                if row is None:
                    raise AIError("SCRIPT_VERSION_CONFLICT", 409)
                record = dict(row)
            else:
                record = dict(script_id=str(uuid4()), tenant_id=tenant, name="AI generated analytics",
                    source_code=source, created_at=now, modified_at=now, version=1)
                db.execute("INSERT INTO scripts VALUES (:script_id,:tenant_id,:name,:source_code,:created_at,:modified_at,:version)", record)
            db.execute("INSERT OR REPLACE INTO ai_scripts VALUES (?,?,?,?,?,?,?,?,?,?)", (
                record["script_id"], tenant, record["version"], task, signature(task),
                json.dumps(reference, sort_keys=True), fingerprint(schema),
                json.dumps(parameters, sort_keys=True), fingerprint(source), model))
            db.execute("INSERT INTO ai_events VALUES (?,?,?,?,?,?,?)", (str(uuid4()), tenant,
                "modify_saved" if previous else "generate_saved", record["script_id"], "saved", now,
                json.dumps(metrics, allow_nan=False)))
            return record

    def event(self, tenant, operation, script_id, status, metrics):
        with self.store.connection() as db:
            db.execute("INSERT INTO ai_events VALUES (?,?,?,?,?,?,?)", (str(uuid4()), tenant, operation,
                       script_id, status, timestamp(), json.dumps(metrics, allow_nan=False)))
