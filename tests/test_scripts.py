"""Tenant, persistence, mock execution and audit acceptance tests."""

import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import sqlite3
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from codebound.auth import Principal
from codebound.generate_data import generate_dataset
from codebound.main import create_app

TOKENS = {"trusted-test-context-a": Principal("demo-tenant-1"),
          "trusted-test-context-b": Principal("demo-tenant-2")}
SOURCE = "# codebound-mock:september_totals"
BODY = {"name": "September totals", "source_code": SOURCE}
EXECUTE = {"parameters": {"year": 2026}, "reference": {"kind": "dataset", "id": "railway-demo"}}


@pytest.fixture
def setup(tmp_path):
    generate_dataset(tmp_path)
    app = create_app(tmp_path, mock_tokens=TOKENS)
    a = TestClient(app, headers={"Authorization": "Bearer trusted-test-context-a"})
    b = TestClient(app, headers={"Authorization": "Bearer trusted-test-context-b"})
    return app, a, b, tmp_path


def new_script(client, **overrides):
    response = client.post("/scripts", json={**BODY, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def audits(app):
    with sqlite3.connect(app.state.store.path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM execution_audit ORDER BY timestamp")]


def test_script_crud_and_version(setup):
    _, a, _, _ = setup
    first = new_script(a)
    UUID(first["script_id"])
    assert first["tenant_id"] == "demo-tenant-1"
    assert first["version"] == 1
    assert datetime.fromisoformat(first["created_at"]).utcoffset().total_seconds() == 0
    assert first["created_at"] == first["modified_at"]
    path = "/scripts/" + first["script_id"]
    assert a.get(path).json() == first
    assert a.get("/scripts").json() == [first]
    update = a.put(path, json={"name": "New name", "source_code": "# codebound-mock:ticket_totals"})
    assert update.status_code == 200
    second = update.json()
    assert second["version"] == 2
    assert second["script_id"] == first["script_id"]
    assert second["created_at"] == first["created_at"]
    assert second["modified_at"] >= first["modified_at"]
    assert a.get(path).json() == second
    assert a.delete(path).status_code == 204
    assert a.get(path).status_code == 404
    assert a.get("/scripts").json() == []
    assert a.delete(path).status_code == 404


@pytest.mark.parametrize("owner_index,outsider_index,dataset", [(1, 2, "railway-demo"), (2, 1, "lufthansa-demo")])
def test_tenant_crud_and_execution_isolation(setup, owner_index, outsider_index, dataset):
    app = setup[0]
    owner, outsider = setup[owner_index], setup[outsider_index]
    script = new_script(owner)
    path = "/scripts/" + script["script_id"]
    assert outsider.get("/scripts").json() == []
    assert outsider.get(path).status_code == 404
    assert outsider.put(path, json=BODY).status_code == 404
    assert outsider.delete(path).status_code == 404
    response = outsider.post(path + "/execute", json={**EXECUTE, "reference": {"id": dataset}})
    assert response.status_code == 404
    assert response.json()["status"] == "denied"
    assert response.json()["result"] is None
    assert owner.get(path).json() == script
    event = audits(app)[-1]
    assert event["tenant_id"] != script["tenant_id"]
    assert event["operation"] == "unresolved"
    assert event["script_version"] is None
    assert event["status"] == "denied"


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/scripts", BODY), ("GET", "/scripts", None),
    ("GET", "/scripts/{id}", None), ("PUT", "/scripts/{id}", BODY),
    ("DELETE", "/scripts/{id}", None), ("POST", "/scripts/{id}/execute", EXECUTE),
    ("GET", "/datasets", None), ("GET", "/datasets/railway-demo/aggregations/september", None),
])
def test_authentication_required_and_tenant_header_not_trusted(setup, method, path, body):
    app, a, _, _ = setup
    script = new_script(a)
    anonymous = TestClient(app)
    path = path.replace("{id}", script["script_id"])
    for headers in ({}, {"X-Tenant-ID": "demo-tenant-1"}, {"Authorization": "Bearer demo-tenant-1"}):
        response = anonymous.request(method, path, json=body, headers=headers)
        assert response.status_code == 401
    assert audits(app) == []


def test_default_app_fails_closed(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/scripts").status_code == 401


def test_identity_spoofing_rejected(setup):
    _, a, b, _ = setup
    assert b.post("/scripts", json={**BODY, "tenant_id": "demo-tenant-1"}).status_code == 422
    created = b.post("/scripts", json=BODY, headers={"X-Tenant-ID": "demo-tenant-1"}).json()
    assert created["tenant_id"] == "demo-tenant-2"
    assert a.get("/scripts/" + created["script_id"]).status_code == 404
    assert b.put("/scripts/" + created["script_id"], json={**BODY, "version": 99}).status_code == 422


@pytest.mark.parametrize("tenant_index,dataset", [(1, "railway-demo"), (2, "lufthansa-demo")])
def test_scoped_mock_execution_and_dataset_endpoints(setup, tenant_index, dataset):
    app, _, _, tmp_path = setup
    client = setup[tenant_index]
    script = new_script(client)
    tenant = script["tenant_id"]
    with (tmp_path / "railway.csv").open(newline="", encoding="utf-8") as source:
        rows = [row for row in csv.DictReader(source)
                if row["tenant_id"] == tenant and row["travel_date"].startswith("2026-09-")]
    response = client.post("/scripts/" + script["script_id"] + "/execute",
                           json={**EXECUTE, "reference": {"id": dataset}})
    assert response.status_code == 200, response.text
    outcome = response.json()
    UUID(outcome["execution_id"])
    assert outcome["status"] == "succeeded"
    assert outcome["error"] is None
    assert outcome["duration_ms"] >= 0
    assert outcome["result"]["ticket_count"] == len(rows) == 45
    assert outcome["result"]["total_revenue_cents"] == sum(int(r["price_cents"]) for r in rows)
    assert len(response.content) < 500
    assert client.get(f"/datasets/{dataset}/aggregations/september").json() == outcome["result"]
    listed = client.get("/datasets").json()["datasets"]
    assert [row["id"] for row in listed] == [dataset]
    event = audits(app)[0]
    assert event["execution_id"] == outcome["execution_id"]
    assert event["tenant_id"] == tenant
    assert event["script_id"] == script["script_id"]
    assert event["script_version"] == 1
    assert event["operation"] == "september_totals"
    assert event["status"] == "succeeded"
    assert event["duration_ms"] == outcome["duration_ms"]
    datetime.fromisoformat(event["timestamp"])


@pytest.mark.parametrize("tenant_index,other_dataset", [(1, "lufthansa-demo"), (2, "railway-demo")])
def test_dataset_cross_tenant_access_denied(setup, tenant_index, other_dataset):
    app = setup[0]
    client = setup[tenant_index]
    script = new_script(client)
    assert client.get(f"/datasets/{other_dataset}/aggregations/september").status_code == 404
    response = client.post(f"/scripts/{script['script_id']}/execute",
                           json={**EXECUTE, "reference": {"id": other_dataset}})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"
    assert audits(app)[0]["status"] == "denied"


@pytest.mark.parametrize("source", [
    "print('hello')", "__import__('os').system('whoami')", "eval('1+1')",
    "# codebound-mock:september_totals\nprint('injected')",
    "# codebound-mock:unknown", "# codebound-mock:september_totals ",
])
def test_unsupported_source_never_interpreted(setup, source):
    app, a, _, _ = setup
    script = new_script(a, source_code=source)
    response = a.post(f"/scripts/{script['script_id']}/execute", json=EXECUTE)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_OPERATION"
    assert response.json()["result"] is None
    assert audits(app)[0]["status"] == "failed"
    assert source not in json.dumps(audits(app))


def test_source_cannot_write_host_file(setup):
    _, a, _, tmp_path = setup
    target = tmp_path / "must-not-exist.txt"
    script = new_script(a, source_code=f"open({str(target)!r}, 'w').write('unsafe')")
    assert a.post(f"/scripts/{script['script_id']}/execute", json=EXECUTE).status_code == 422
    assert not target.exists()


def test_all_year_totals_with_ticket_type_filter(setup):
    _, a, _, tmp_path = setup
    script = new_script(a, source_code="# codebound-mock:ticket_totals")
    with (tmp_path / "railway.csv").open(newline="", encoding="utf-8") as source:
        rows = [r for r in csv.DictReader(source) if r["tenant_id"] == "demo-tenant-1"
                and r["ticket_type"] == "flex"]
    outcome = a.post(f"/scripts/{script['script_id']}/execute", json={**EXECUTE,
        "parameters": {"year": 2026, "ticket_type": "flex"}}).json()
    assert outcome["status"] == "succeeded"
    assert outcome["result"]["month"] is None
    assert outcome["result"]["ticket_count"] == len(rows)
    assert outcome["result"]["total_revenue_cents"] == sum(int(r["price_cents"]) for r in rows)


def test_connector_reference_is_explicitly_unimplemented(setup):
    app, a, _, _ = setup
    script = new_script(a)
    response = a.post(f"/scripts/{script['script_id']}/execute",
                      json={"reference": {"kind": "connector", "id": "not-a-url"}})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "CONNECTOR_NOT_IMPLEMENTED"
    assert audits(app)[0]["status"] == "failed"


def test_sqlite_persists_scripts_versions_audits_and_deletion(setup):
    app, a, _, tmp_path = setup
    script = new_script(a)
    path = f"/scripts/{script['script_id']}"
    updated = a.put(path, json=BODY).json()
    a.post(path + "/execute", json=EXECUTE)
    restarted_app = create_app(tmp_path, mock_tokens=TOKENS)
    restarted = TestClient(restarted_app, headers=dict(a.headers))
    assert restarted.get(path).json() == updated
    assert audits(restarted_app) == audits(app)
    assert restarted.delete(path).status_code == 204
    third = TestClient(create_app(tmp_path, mock_tokens=TOKENS), headers=dict(a.headers))
    assert third.get(path).status_code == 404
    assert len(audits(restarted_app)) == 1


def test_concurrent_updates_do_not_lose_version_increments(setup):
    app, a, _, _ = setup
    script = new_script(a)
    def update(index):
        return app.state.store.update("demo-tenant-1", script["script_id"], str(index), SOURCE)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(update, range(8)))
    assert sorted(r["version"] for r in records) == list(range(2, 10))
    assert a.get(f"/scripts/{script['script_id']}").json()["version"] == 9


def test_sql_injection_is_stored_as_text(setup):
    _, a, _, _ = setup
    payload = "'); DROP TABLE scripts; --"
    created = new_script(a, name=payload, source_code=payload)
    assert a.get(f"/scripts/{created['script_id']}").json()["name"] == payload
    assert len(a.get("/scripts").json()) == 1


@pytest.mark.parametrize("body", [
    {**BODY, "name": ""}, {**BODY, "name": " "}, {**BODY, "source_code": ""},
    {**BODY, "name": "x" * 121}, {**BODY, "source_code": "x" * 32001},
])
def test_script_input_bounds(setup, body):
    _, a, _, _ = setup
    assert a.post("/scripts", json=body).status_code == 422


@pytest.mark.parametrize("body", [
    {**EXECUTE, "parameters": {"year": 0}},
    {**EXECUTE, "parameters": {"year": "2026"}},
    {**EXECUTE, "parameters": {"ticket_type": "secret"}},
    {**EXECUTE, "parameters": {"tenant_id": "demo-tenant-2"}},
    {**EXECUTE, "reference": {"id": "railway-demo", "tenant_id": "demo-tenant-2"}},
    {**EXECUTE, "code": "print('injected')"},
])
def test_execution_input_bounds(setup, body):
    app, a, _, _ = setup
    script = new_script(a)
    assert a.post(f"/scripts/{script['script_id']}/execute", json=body).status_code == 422
    assert audits(app) == []


def test_audit_excludes_source_parameters_and_secrets(setup):
    app, a, _, _ = setup
    script = new_script(a, source_code="secret-marker-never-log")
    a.post(f"/scripts/{script['script_id']}/execute", json=EXECUTE)
    log = json.dumps(audits(app))
    for forbidden in ("secret-marker-never-log", "trusted-test-context-a", "parameters", "result", "price_cents"):
        assert forbidden not in log


def test_failed_executor_is_audited_with_generic_error(setup):
    app, a, _, _ = setup
    class BrokenExecutor:
        def execute(self, **kwargs):
            raise RuntimeError("secret database details")
    app.state.executor = BrokenExecutor()
    script = new_script(a)
    response = a.post(f"/scripts/{script['script_id']}/execute", json=EXECUTE)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EXECUTOR_UNAVAILABLE"
    assert "secret" not in response.text
    assert audits(app)[0]["status"] == "failed"


def test_audit_failure_prevents_result_release(setup, monkeypatch):
    app, a, _, _ = setup
    script = new_script(a)
    def fail(**kwargs):
        raise sqlite3.OperationalError("sensitive storage error")
    monkeypatch.setattr(app.state.store, "audit", fail)
    client = TestClient(app, headers=dict(a.headers), raise_server_exceptions=False)
    response = client.post(f"/scripts/{script['script_id']}/execute", json=EXECUTE)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUDIT_UNAVAILABLE"
    assert response.json()["result"] is None
    assert "ticket_count" not in response.text
    assert "sensitive" not in response.text


def test_missing_script_and_invalid_uuid(setup):
    _, a, _, _ = setup
    assert a.get(f"/scripts/{uuid4()}").status_code == 404
    assert a.get("/scripts/not-a-uuid").status_code == 422


def test_demo_factory_requires_explicit_tokens(monkeypatch):
    from codebound.demo import create_demo_app
    monkeypatch.delenv("CODEBOUND_DEMO_TOKEN_A", raising=False)
    monkeypatch.delenv("CODEBOUND_DEMO_TOKEN_B", raising=False)
    with pytest.raises(RuntimeError):
        create_demo_app()


def test_mock_empty_period(setup):
    _, a, _, _ = setup
    script = new_script(a)
    response = a.post(f"/scripts/{script['script_id']}/execute",
                      json={**EXECUTE, "parameters": {"year": 2025}})
    assert response.status_code == 200
    assert response.json()["result"]["ticket_count"] == 0
    assert response.json()["result"]["total_revenue_cents"] == 0


def test_listing_pagination_stays_tenant_scoped(setup):
    _, a, b, _ = setup
    records = [new_script(a, name=str(i)) for i in range(3)]
    new_script(b)
    assert a.get("/scripts?limit=1&offset=1").json() == [records[1]]
    assert a.get("/scripts?limit=101").status_code == 422


@pytest.mark.parametrize("missing", [True, False])
def test_mock_missing_or_corrupt_data_is_audited(setup, missing):
    app, a, _, tmp_path = setup
    script = new_script(a)
    if missing:
        (tmp_path / "railway.csv").unlink()
    else:
        (tmp_path / "railway.csv").write_text("bad-data", encoding="utf-8")
    response = a.post(f"/scripts/{script['script_id']}/execute", json=EXECUTE)
    assert response.status_code == (404 if missing else 503)
    assert response.json()["result"] is None
    assert audits(app)[0]["status"] == "failed"
