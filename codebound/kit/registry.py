"""Workspace/user-bound script registry, metadata and sanitized audit in existing SQLite."""
import json
from uuid import uuid4
from codebound.storage import ScriptStore, timestamp
from codebound.llm.store import fingerprint
from codebound.kit.config import ROOT

class Registry:
    def __init__(self, path=None):
        self.store=ScriptStore(path if path is not None else ROOT/'data/codebound.sqlite3')
        with self.store.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS kit_scripts (
              script_id TEXT PRIMARY KEY, owner TEXT NOT NULL, bindings_json TEXT NOT NULL,
              identity TEXT, name TEXT NOT NULL, model TEXT);
            CREATE TABLE IF NOT EXISTS kit_events (
              event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, workspace TEXT NOT NULL,
              actor TEXT NOT NULL, script_id TEXT, status TEXT NOT NULL, code TEXT, metrics_json TEXT NOT NULL);
            ''')
    def create(self, workspace, actor, name, source, bindings, identity=None, model=None):
        record=self.store.create(workspace,name,source)
        with self.store.connection() as db:
            db.execute('INSERT INTO kit_scripts VALUES (?,?,?,?,?,?)',(record['script_id'],actor,json.dumps(sorted(bindings)),identity,name,model))
        return record
    def get(self, workspace, sid):
        record=self.store.get(workspace,sid)
        if record is None: raise KeyError('SCRIPT_NOT_FOUND')
        with self.store.connection() as db:
            meta=db.execute('SELECT * FROM kit_scripts WHERE script_id=?',(sid,)).fetchone()
        if meta is None: raise KeyError('SCRIPT_NOT_FOUND')
        return record,dict(meta)
    def reusable(self, workspace, identity):
        with self.store.connection() as db:
            rows=db.execute("SELECT s.script_id FROM scripts s JOIN kit_scripts k ON k.script_id=s.script_id WHERE s.tenant_id=? AND k.identity=? AND EXISTS (SELECT 1 FROM kit_events e WHERE e.script_id=s.script_id AND e.status='succeeded' AND e.code IS NULL) ORDER BY s.created_at DESC",(workspace,identity)).fetchall()
        return self.get(workspace,rows[0][0]) if rows else None
    def event(self, workspace, actor, sid, status, code, metrics):
        with self.store.connection() as db:
            db.execute('INSERT INTO kit_events VALUES (?,?,?,?,?,?,?,?)',(str(uuid4()),timestamp(),workspace,actor,sid,status,code,json.dumps(metrics,allow_nan=False)))
