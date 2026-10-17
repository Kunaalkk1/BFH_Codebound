"""Local policy tests only. These do NOT certify an isolated Python runtime."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
import pytest

from codebound.auth import Principal
from codebound.execution import MockSandboxExecutor
from codebound.generate_data import generate_dataset
from codebound.main import create_app
from codebound.sandbox import ExecutionState, SandboxSettings

SOURCE = {"name": "demo", "source_code": "# codebound-mock:september_totals"}
REQUEST = {"reference": {"id": "railway-demo"}}


def client_for(tmp_path, *, settings=None, executor=None):
    generate_dataset(tmp_path)
    app = create_app(tmp_path, settings=settings or SandboxSettings(), executor=executor,
                     mock_tokens={"test-a": Principal("demo-tenant-1"),
                                  "test-b": Principal("demo-tenant-2")})
    return TestClient(app, headers={"Authorization": "Bearer test-a"})


def script_path(client):
    response = client.post("/scripts", json=SOURCE)
    assert response.status_code == 201
    return f"/scripts/{response.json()['script_id']}/execute"


def test_local_docker_mode_disallows_executor_override(tmp_path):
    with pytest.raises(ValueError, match="overrides are disabled"):
        create_app(tmp_path, settings=SandboxSettings(mode="local_docker"),
                   executor=MockSandboxExecutor(tmp_path))


def test_mock_lifecycle_states(tmp_path):
    client = client_for(tmp_path)
    path = script_path(client)
    assert client.post(path, json=REQUEST).json()["state"] == "COMPLETED"
    client.put(path.removesuffix("/execute"), json={**SOURCE, "source_code": "print('not run')"})
    response = client.post(path, json=REQUEST)
    assert response.status_code == 422
    assert response.json()["state"] == "REJECTED"
    assert set(ExecutionState) == {"PENDING", "RUNNING", "COMPLETED", "FAILED", "TIMED_OUT", "REJECTED"}


def test_result_limit_rejects_without_returning_data_and_releases_slot(tmp_path):
    class LargeResult:
        def execute(self, **kwargs):
            return {"value": "private-value" * 100}
    client = client_for(tmp_path, settings=SandboxSettings(max_result_bytes=256), executor=LargeResult())
    path = script_path(client)
    for _ in range(3):
        response = client.post(path, json=REQUEST)
        assert response.status_code == 413
        assert response.json()["result"] is None
        assert response.json()["state"] == "FAILED"
        assert response.json()["error"]["code"] == "OUTPUT_LIMIT_EXCEEDED"
        assert "private-value" not in response.text


def test_concurrency_cap_is_per_tenant_and_slot_is_released(tmp_path):
    entered, release = Event(), Event()
    class BlockingExecutor:
        def execute(self, *, tenant_id, **kwargs):
            if tenant_id == "demo-tenant-1":
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test coordination timed out")
            return {"ticket_count": 0}
    client = client_for(tmp_path, settings=SandboxSettings(max_concurrent_per_tenant=1),
                        executor=BlockingExecutor())
    path_a = script_path(client)
    b = TestClient(client.app, headers={"Authorization": "Bearer test-b"})
    path_b = script_path(b)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, path_a, json=REQUEST)
        try:
            assert entered.wait(5)
            response = client.post(path_a, json=REQUEST)
            assert response.status_code == 429
            assert response.json()["state"] == "REJECTED"
            assert b.post(path_b, json={"reference": {"id": "lufthansa-demo"}}).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    assert client.post(path_a, json=REQUEST).status_code == 200


@pytest.mark.parametrize("kwargs", [
    {"mode": "auto"}, {"mode": "isolated"}, {"max_concurrent_per_tenant": 0}, {"max_concurrent_per_tenant": 33},
    {"max_result_bytes": 255}, {"max_result_bytes": 1_048_577},
])
def test_invalid_server_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        SandboxSettings(**kwargs)


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("SANDBOX_MODE", "local_docker")
    monkeypatch.setenv("SANDBOX_MAX_CONCURRENT", "3")
    monkeypatch.setenv("SANDBOX_MAX_RESULT_BYTES", "4096")
    assert SandboxSettings.from_env() == SandboxSettings("local_docker", 3, 4096)


def test_script_parameters_cannot_change_limits(tmp_path):
    client = client_for(tmp_path)
    response = client.post(script_path(client), json={**REQUEST, "parameters": {"timeout": 999}})
    assert response.status_code == 422
