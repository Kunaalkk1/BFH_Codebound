import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from codebound.auth import Principal
from codebound.generate_data import generate_dataset
from codebound.llm.client import AIError, OllamaClient, OllamaSettings
from codebound.llm.models import GenerateRequest
from codebound.llm.validator import validate_source
from codebound.main import create_app
from codebound.sandbox import SandboxSettings

SOURCE = "import json\npayload=json.load(open('/tmp/input.json', encoding='utf-8'))\nprint(json.dumps({'ticket_count':len(payload['records'])}))\n"
BODY = {"task": "Calculate ticket revenue.", "reference": {"kind": "dataset", "id": "railway-demo"}}


class FakeOllama:
    settings = SimpleNamespace(model="local-test-model")
    def __init__(self):
        self.calls, self.prompts, self.failure, self.source = 0, [], None, SOURCE
    def ready(self):
        return {"model": self.settings.model}
    def generate(self, prompt, metrics):
        self.calls += 1
        self.prompts.append(prompt)
        metrics.update(ollama_calls=1, generation_ms=1)
        if self.failure:
            raise self.failure
        return self.source


@pytest.fixture
def env(tmp_path):
    generate_dataset(tmp_path)
    llm = FakeOllama()
    app = create_app(tmp_path, mock_tokens={"a": Principal("demo-tenant-1"), "b": Principal("demo-tenant-2")}, ollama_client=llm)
    a, b = (TestClient(app, headers={"Authorization": "Bearer " + token}) for token in ("a", "b"))
    return app, a, b, llm


def test_generate_only_never_executes_and_prompt_has_no_rows(env, monkeypatch):
    app, a, _, llm = env
    monkeypatch.setattr(app.state.executor, "execute", lambda **kwargs: pytest.fail("Generated code executed on generate-only"))
    response = a.post("/ai/scripts/generate", json=BODY)
    assert response.status_code == 200
    assert response.json()["version"] == 1 and llm.calls == 1
    assert "records\": [" not in llm.prompts[0]
    for forbidden in ("demo-tenant-1", "demo-customer", "trusted-test", "postgresql://"):
        assert forbidden not in llm.prompts[0]
    script = a.get("/scripts/" + response.json()["script_id"]).json()
    assert script["tenant_id"] == "demo-tenant-1" and script["source_code"] == SOURCE


@pytest.mark.parametrize("reference", [{"kind": "dataset", "id": "lufthansa-demo"}, {"kind": "connector", "id": "other"}])
def test_generation_denies_bad_dataset_before_ollama(env, reference):
    _, a, _, llm = env
    response = a.post("/ai/scripts/generate", json={**BODY, "reference": reference})
    assert response.status_code in {404, 503}
    assert llm.calls == 0


def test_auth_and_payload_validation(env):
    app, a, _, llm = env
    assert TestClient(app).post("/ai/scripts/generate", json=BODY).status_code == 401
    for body in [{**BODY, "tenant_id": "b"}, {**BODY, "task": " "}, {**BODY, "task": "x" * 2001},
                 {**BODY, "parameters": {"month": 13}}]:
        assert a.post("/ai/scripts/generate", json=body).status_code == 422
    assert llm.calls == 0


def test_modify_ownership_version_and_failed_generation(env):
    _, a, b, llm = env
    first = a.post("/ai/scripts/generate", json=BODY).json()
    sid = first["script_id"]
    path = f"/ai/scripts/{sid}/modify"
    assert b.post(path, json={"instruction": "Change it"}).status_code == 404
    assert a.post(path, json={"instruction": "Change it", "expected_version": 99}).status_code == 409
    assert llm.calls == 1
    llm.source = SOURCE + "\n# valid modification\n"
    updated = a.post(path, json={"instruction": "Add an average", "expected_version": 1})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    llm.failure = AIError("OLLAMA_TIMEOUT", 504)
    assert a.post(path, json={"instruction": "Change again"}).status_code == 504
    assert a.get("/scripts/" + sid).json()["version"] == 2


def test_concurrent_edit_is_not_overwritten(env):
    app, a, _, llm = env
    sid = a.post("/ai/scripts/generate", json=BODY).json()["script_id"]
    def changed(prompt, metrics):
        app.state.store.update("demo-tenant-1", sid, "Manual edit", SOURCE + "\n# manual\n")
        return SOURCE + "\n# generated edit\n"
    llm.generate = changed
    assert a.post(f"/ai/scripts/{sid}/modify", json={"instruction": "Change"}).status_code == 409
    assert a.get("/scripts/" + sid).json()["name"] == "Manual edit"


def test_exact_reuse_defaults_and_version_invalidation(env):
    app, a, _, llm = env
    body = GenerateRequest.model_validate(BODY)
    service = app.state.ai
    first, _ = service.generate("demo-tenant-1", body, {})
    app.state.store.audit(execution_id="unit-success", tenant_id="demo-tenant-1", script_id=first["script_id"],
        script_version=1, operation="python", status="succeeded", duration_ms=1, execution_mode="local_docker")
    metrics = {}
    second, _ = service.generate("demo-tenant-1", body, metrics, reuse=True)
    assert second["script_id"] == first["script_id"] and metrics["script_reused"] and llm.calls == 1
    # Explicit defaults canonicalize to the same parameter contract.
    explicit = body.model_copy(update={"parameters": body.parameters.model_copy()})
    assert service.generate("demo-tenant-1", explicit, {}, reuse=True)[0]["script_id"] == first["script_id"]
    app.state.store.update("demo-tenant-1", first["script_id"], "manual", SOURCE)
    assert service.generate("demo-tenant-1", body, {}, reuse=True)[0]["script_id"] != first["script_id"]
    assert llm.calls == 2


def test_reuse_rejects_parameter_and_schema_mismatch(env, monkeypatch):
    app, _, _, llm = env
    service = app.state.ai
    body = GenerateRequest.model_validate(BODY)
    service.generate("demo-tenant-1", body, {})
    changed = GenerateRequest.model_validate({**BODY, "parameters": {"year": 2025}})
    service.generate("demo-tenant-1", changed, {}, reuse=True)
    assert llm.calls == 2
    schema = service.schema("demo-tenant-1", body.reference)
    monkeypatch.setattr(service, "schema", lambda *args: {**schema, "contract_version": 2})
    service.generate("demo-tenant-1", body, {}, reuse=True)
    assert llm.calls == 3


def test_no_cross_tenant_or_ambiguous_reuse(env):
    app, _, _, llm = env
    service = app.state.ai
    body = GenerateRequest.model_validate(BODY)
    first, _ = service.generate("demo-tenant-1", body, {})
    # A second explicit generation creates ambiguity; neither is arbitrarily reused.
    second, _ = service.generate("demo-tenant-1", body, {})
    for record in (first, second):
        app.state.store.audit(execution_id=record["script_id"], tenant_id="demo-tenant-1", script_id=record["script_id"],
            script_version=1, operation="python", status="succeeded", duration_ms=1, execution_mode="local_docker")
    third, _ = service.generate("demo-tenant-1", body, {}, reuse=True)
    assert third["script_id"] != first["script_id"] and llm.calls == 3
    b = GenerateRequest.model_validate({**BODY, "reference": {"kind": "dataset", "id": "lufthansa-demo"}})
    record, _ = service.generate("demo-tenant-2", b, {}, reuse=True)
    assert record["tenant_id"] == "demo-tenant-2" and llm.calls == 4


def test_ai_audit_is_minimal_and_repair_disabled(env):
    app, a, _, _ = env
    a.post("/ai/scripts/generate", json=BODY)
    with app.state.store.connection() as db:
        events = [dict(row) for row in db.execute("SELECT * FROM ai_events")]
    assert len(events) == 2
    audit = json.dumps(events)
    assert SOURCE not in audit and BODY["task"] not in audit and "source_code" not in audit
    assert all(json.loads(event["metrics_json"])["repair_attempts"] == 0 for event in events)
    assert a.get("/ai/health").json()["automatic_repair"] is False


def test_audit_write_failure_rolls_back_modified_source(env):
    app, a, _, llm = env
    sid = a.post("/ai/scripts/generate", json=BODY).json()["script_id"]
    with app.state.store.connection() as db:
        db.execute("CREATE TRIGGER fail_ai_audit BEFORE INSERT ON ai_events BEGIN SELECT RAISE(ABORT, 'audit down'); END")
    llm.source = SOURCE + "\n# new source\n"
    assert a.post(f"/ai/scripts/{sid}/modify", json={"instruction": "change"}).status_code == 503
    assert a.get("/scripts/" + sid).json()["source_code"] == SOURCE


@pytest.mark.parametrize("source,code", [("x =", "INVALID_PYTHON_SOURCE"), ("x" * 32001, "SOURCE_LIMIT_EXCEEDED"),
    (SOURCE.replace("import json", "import os\nimport json"), "INCOMPATIBLE_IMPORT"),
    (SOURCE.replace("/tmp/input.json", "/etc/passwd"), "INCOMPATIBLE_FILE_ACCESS"),
    (SOURCE + "eval('x')", "INCOMPATIBLE_SOURCE"), ("print('hello')", "INVALID_WORKER_OUTPUT_CONTRACT")])
def test_validator_rejects_incompatible_source(source, code):
    with pytest.raises(AIError, match=code):
        validate_source(source)


def test_valid_source_is_not_executed_by_validator():
    assert validate_source(SOURCE) == SOURCE


@pytest.mark.parametrize("failure", [AIError("OLLAMA_UNAVAILABLE"), AIError("OLLAMA_TIMEOUT", 504)])
def test_unavailable_has_no_fallback_and_no_saved_script(env, failure):
    _, a, _, llm = env
    llm.failure = failure
    response = a.post("/ai/scripts/generate", json=BODY)
    assert response.status_code == failure.status and response.json()["error"]["code"] == failure.code
    assert a.get("/scripts").json() == [] and llm.calls == 1


def test_run_rejects_mock_runtime_before_generation(env):
    _, a, _, llm = env
    assert a.post("/ai/tasks/run", json=BODY).status_code == 409
    assert llm.calls == 0


def test_unexecuted_script_is_not_automatically_reused(env):
    app, a, _, llm = env
    first = a.post("/ai/scripts/generate", json=BODY).json()
    record, _ = app.state.ai.generate("demo-tenant-1", GenerateRequest.model_validate(BODY), {}, reuse=True)
    assert record["script_id"] != first["script_id"] and llm.calls == 2


def test_source_unchanged_does_not_bump_version(env):
    _, a, _, _ = env
    sid = a.post("/ai/scripts/generate", json=BODY).json()["script_id"]
    response = a.post(f"/ai/scripts/{sid}/modify", json={"instruction": "Change it"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "SOURCE_UNCHANGED"
    assert a.get("/scripts/" + sid).json()["version"] == 1


def test_run_orchestration_uses_existing_execution_audit_and_reuse(tmp_path, monkeypatch):
    generate_dataset(tmp_path)
    llm = FakeOllama()
    app = create_app(tmp_path, settings=SandboxSettings(mode="local_docker"),
                     mock_tokens={"a": Principal("demo-tenant-1")}, ollama_client=llm)
    a = TestClient(app, headers={"Authorization": "Bearer a"})
    monkeypatch.setattr(app.state.executor, "execute", lambda **kw: {"unit_result": True})
    first = a.post("/ai/tasks/run", json=BODY).json()
    assert first["execution"]["status"] == "succeeded" and first["ollama_called"]
    second = a.post("/ai/tasks/run", json=BODY).json()
    assert second["script_id"] == first["script_id"] and not second["ollama_called"]
    assert llm.calls == 1
    with app.state.store.connection() as db:
        assert db.execute("SELECT count(*) FROM execution_audit").fetchone()[0] == 2


def test_client_model_and_structured_request(monkeypatch):
    client = OllamaClient(OllamaSettings(model="test:local"))
    captured = []
    def request(method, path, body=None, **kwargs):
        captured.append((method, path, body))
        if method == "GET":
            return {"models": [{"name": "test:local", "digest": "digest", "size": 123}]}
        return {"done": True, "response": json.dumps({"source_code": SOURCE}), "prompt_eval_count": 15, "eval_count": 20}
    monkeypatch.setattr(client, "request", request)
    metrics = {}
    assert client.generate("minimum task", metrics) == SOURCE
    assert captured[-1][2]["format"]["additionalProperties"] is False
    assert captured[-1][2]["stream"] is False
    assert metrics["ollama_calls"] == 1 and metrics["eval_count"] == 20


@pytest.mark.parametrize("entries,code", [([], "LOCAL_MODEL_UNAVAILABLE"),
    ([{"name": "test:local", "remote_host": "cloud", "size": 1, "digest": "x"}], "REMOTE_MODEL_FORBIDDEN")])
def test_client_missing_or_remote_model(monkeypatch, entries, code):
    client = OllamaClient(OllamaSettings(model="test:local"))
    monkeypatch.setattr(client, "request", lambda *a, **k: {"models": entries})
    with pytest.raises(AIError, match=code):
        client.ready()


@pytest.mark.parametrize("response", [{"done": False}, {"done": True, "response": "not JSON"},
    {"done": True, "response": '{"source_code":"x","tenant_id":"b"}'}])
def test_invalid_model_structured_response(monkeypatch, response):
    client = OllamaClient(OllamaSettings(model="test:local"))
    monkeypatch.setattr(client, "ready", lambda: {})
    monkeypatch.setattr(client, "request", lambda *a, **k: response)
    with pytest.raises(AIError):
        client.generate("task", {})


def test_prompt_limits_and_generation_concurrency(monkeypatch):
    client = OllamaClient(OllamaSettings(model="test:local", max_prompt_bytes=5))
    with pytest.raises(AIError, match="PROMPT_LIMIT"):
        client.generate("123456", {})
    client.slot.acquire()
    try:
        with pytest.raises(AIError, match="OLLAMA_BUSY"):
            client.generate("task", {})
    finally:
        client.slot.release()


def test_connection_timeout_and_sanitized_errors(monkeypatch):
    class Connection:
        def __init__(self, host, port, timeout):
            assert host == "127.0.0.1" and port == 11434 and timeout == 3
        def connect(self):
            raise TimeoutError("private network details")
        def close(self):
            pass
    monkeypatch.setattr("http.client.HTTPConnection", Connection)
    with pytest.raises(AIError, match="OLLAMA_TIMEOUT") as failure:
        OllamaClient().request("GET", "/api/tags")
    assert "private" not in str(failure.value)
