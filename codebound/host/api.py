"""Codebound scripts scoped to real gateway users and organizations."""
import ast
import json
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from codebound.broker_runtime import execute
from codebound.docker_executor import LocalDockerExecutor
from codebound.host.gateway import Gateway, Broker
from codebound.kit.registry import Registry
from codebound.llm.client import OllamaClient, AIError
from codebound.sandbox import SandboxSettings
from codebound.storage import timestamp

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = SandboxSettings(mode='local_docker', worker_image='codebound-worker:host', timeout_seconds=15)
ToolName = Annotated[str, Field(min_length=1, max_length=256)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Strict):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=1024)


class Create(Strict):
    name: str = Field(min_length=1, max_length=120)
    source_code: str = Field(min_length=1, max_length=32000)
    bindings: list[ToolName] = Field(default_factory=list, max_length=100)


class Update(Create):
    expected_version: int = Field(ge=1)


class Run(Strict):
    server_id: str = Field(min_length=1, max_length=128)
    parameters: dict = Field(default_factory=dict)


class Generate(Strict):
    server_id: str = Field(min_length=1, max_length=128)
    task: str = Field(min_length=1, max_length=2000)
    bindings: list[ToolName] = Field(max_length=30)
    name: str = Field(min_length=1, max_length=120)


def create_app(*, gateway=None, db_path=None, runner=None, readiness=None):
    gateway = gateway or Gateway()
    registry = Registry(db_path or ROOT / '.local/host/codebound.sqlite3')
    runner = runner or execute
    readiness = readiness or (lambda: LocalDockerExecutor(None, SETTINGS).ready())
    app = FastAPI(title='Codebound Host', version='0.4.0')
    slots, slot_lock = {}, Lock()
    ollama = OllamaClient()
    app.state.registry = registry

    def identity(header):
        return gateway.authenticate(header)

    def writable(user):
        if user.role not in ('ADMIN', 'EDITOR'):
            raise HTTPException(403, 'Administrator or editor required')

    def source(value, user):
        value = user.sanitize(value)
        if len(value.encode()) > 64000:
            raise HTTPException(422, 'Source byte limit exceeded')
        try:
            ast.parse(value)
        except (SyntaxError, ValueError, RecursionError):
            raise HTTPException(422, 'Invalid Python source') from None
        return value

    def lookup(user, sid):
        try:
            record, meta = registry.get(user.workspace, sid)
        except KeyError:
            raise HTTPException(404, 'Script not found') from None
        if meta['owner'] != user.actor:
            raise HTTPException(404, 'Script not found')
        return record, meta

    @app.get('/', response_class=HTMLResponse)
    def home():
        return (Path(__file__).with_name('index.html')).read_text(encoding='utf-8')

    @app.get('/health')
    def health():
        try:
            readiness()
        except Exception:
            raise HTTPException(503, 'Docker worker unavailable') from None
        return {'status': 'ok', 'environment': 'host', 'sandbox_mode': 'local_docker'}

    @app.post('/session/login')
    def login(body: Login):
        result = gateway.request('POST', 'auth/login', body=body.model_dump())
        return {'accessToken': result['accessToken']}

    @app.get('/v1/me')
    def me(authorization: str | None = Header(None)):
        user = identity(authorization)
        return {'user_id': user.actor, 'workspace': user.workspace, 'role': user.role}

    @app.get('/v1/servers')
    def servers(authorization: str | None = Header(None)):
        return {'servers': gateway.servers(identity(authorization))}

    @app.get('/v1/tools/{server_id}')
    def tools(server_id: str, authorization: str | None = Header(None)):
        user = identity(authorization)
        return {'tools': gateway.client(user, server_id).tools()}

    @app.post('/v1/scripts')
    def create(body: Create, authorization: str | None = Header(None)):
        user = identity(authorization)
        writable(user)
        record = registry.create(user.workspace, user.actor, user.sanitize(body.name),
            source(body.source_code, user), body.bindings)
        return {'script_id': record['script_id'], 'version': record['version']}

    @app.get('/v1/scripts')
    def scripts(authorization: str | None = Header(None), limit: int = 50, offset: int = 0):
        user = identity(authorization)
        if not 1 <= limit <= 100 or offset < 0:
            raise HTTPException(422, 'Invalid pagination')
        with registry.store.connection() as db:
            rows = db.execute('''SELECT s.script_id,s.name,s.version FROM scripts s
                JOIN kit_scripts k ON k.script_id=s.script_id WHERE s.tenant_id=? AND k.owner=?
                ORDER BY s.created_at,s.script_id LIMIT ? OFFSET ?''',
                (user.workspace, user.actor, limit, offset)).fetchall()
        return {'scripts': [dict(row) for row in rows]}

    @app.get('/v1/scripts/{sid}')
    def get(sid: str, authorization: str | None = Header(None)):
        record, meta = lookup(identity(authorization), sid)
        return {**record, 'bindings': json.loads(meta['bindings_json'])}

    @app.put('/v1/scripts/{sid}')
    def update(sid: str, body: Update, authorization: str | None = Header(None)):
        user = identity(authorization)
        writable(user)
        lookup(user, sid)
        clean = source(body.source_code, user)
        with registry.store.connection() as db:
            row = db.execute('''UPDATE scripts SET name=?,source_code=?,modified_at=?,version=version+1
                WHERE tenant_id=? AND script_id=? AND version=? RETURNING version''',
                (user.sanitize(body.name), clean, timestamp(), user.workspace, sid, body.expected_version)).fetchone()
            if row is None:
                raise HTTPException(409, 'Version conflict')
            db.execute('UPDATE kit_scripts SET bindings_json=?,identity=NULL WHERE script_id=?',
                (json.dumps(sorted(set(body.bindings))), sid))
        return {'script_id': sid, 'version': row['version']}

    @app.delete('/v1/scripts/{sid}')
    def delete(sid: str, authorization: str | None = Header(None)):
        user = identity(authorization)
        writable(user)
        lookup(user, sid)
        with registry.store.connection() as db:
            db.execute('DELETE FROM kit_scripts WHERE script_id=?', (sid,))
            db.execute('DELETE FROM scripts WHERE tenant_id=? AND script_id=?', (user.workspace, sid))
        return {'deleted': True}

    @app.post('/v1/scripts/{sid}/execute')
    def run(sid: str, body: Run, authorization: str | None = Header(None)):
        user = identity(authorization)
        record, meta = lookup(user, sid)
        with slot_lock:
            slot = slots.setdefault(user.workspace, BoundedSemaphore(2))
        if not slot.acquire(False):
            raise HTTPException(429, 'Workspace capacity exceeded')
        started, status, code = perf_counter(), 'failed', 'EXECUTION_FAILED'
        metrics = {'workspace_tool_calls': 0}
        try:
            broker = Broker(gateway.client(user, body.server_id), json.loads(meta['bindings_json']), user, metrics)
            result = runner(record['source_code'], body.parameters, broker, metrics,
                settings=SETTINGS, sanitize=user.sanitize)
            status, code = 'succeeded', None
            return {'script_id': sid, 'version': record['version'], 'result': result, 'metrics': metrics}
        except HTTPException:
            code = 'ACCESS_REFUSED'
            raise
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc) in {
                'TIMED_OUT', 'SCRIPT_FAILED', 'CALL_LIMIT_EXCEEDED', 'INPUT_LIMIT_EXCEEDED',
                'OUTPUT_LIMIT_EXCEEDED', 'INVALID_JSON_RESULT', 'MEMORY_LIMIT_EXCEEDED'}:
                code = str(exc)
            raise HTTPException(422, code) from None
        finally:
            slot.release()
            metrics['total_ms'] = round((perf_counter() - started) * 1000, 3)
            registry.event(user.workspace, user.actor, sid, status, code, metrics)

    @app.post('/v1/generate')
    def generate(body: Generate, authorization: str | None = Header(None)):
        user = identity(authorization)
        writable(user)
        tools = gateway.client(user, body.server_id).tools()
        selected = [tool for tool in tools if tool['name'] in body.bindings]
        if {tool['name'] for tool in selected} != set(body.bindings):
            raise HTTPException(403, 'Tool not available for this user')
        prompt = ('Return JSON with source_code containing complete Python. parameters is a dict already provided. '
            'tools.call(name, arguments) calls only the listed tools. Print exactly one compact JSON object. '
            'No network, filesystem data, secrets or third-party packages are available. Task: '
            + user.sanitize(body.task) + '\nAuthorized tool schemas: ' + json.dumps(selected))
        metrics = {}
        try:
            generated = ollama.generate(prompt, metrics)
        except AIError as exc:
            raise HTTPException(exc.status, exc.code) from None
        return create(Create(name=body.name, source_code=generated, bindings=body.bindings), authorization)

    return app
