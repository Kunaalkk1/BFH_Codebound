"""Opt-in real Docker tests. No mocked daemon or worker responses."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from codebound.auth import Principal
from codebound.docker_executor import LABEL, PROJECT_LABEL, LocalDockerExecutor
from codebound.generate_data import generate_dataset
from codebound.main import create_app
from codebound.sandbox import SandboxSettings

pytestmark = [pytest.mark.docker,
              pytest.mark.skipif(os.getenv("CODEBOUND_TEST_DOCKER") != "1",
                                 reason="Opt in with launch/test.ps1 -Docker; requires real Docker")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEBOUND_TEST_SECRET", "test-secret-must-never-enter-worker")
    generate_dataset(tmp_path)
    settings = SandboxSettings(mode="local_docker", timeout_seconds=3, max_concurrent_per_tenant=1)
    app = create_app(tmp_path, settings=settings,
                     mock_tokens={"integration-a": Principal("demo-tenant-1"),
                                  "integration-b": Principal("demo-tenant-2")})
    app.state.executor.ready()  # Missing runtime is a failure, never a skip when opted in.
    a = TestClient(app, headers={"Authorization": "Bearer integration-a"})
    b = TestClient(app, headers={"Authorization": "Bearer integration-b"})
    yield app, a, b
    assert workers(app) == []  # Also checked after failed and timed-out executions.


def workers(app):
    return app.state.executor.command("ps", "-aq", "--filter", "label=" + LABEL,
                                      "--filter", "label=" + PROJECT_LABEL).decode().split()


def run(client, source, dataset="railway-demo"):
    created = client.post("/scripts", json={"name": "Docker integration", "source_code": source})
    assert created.status_code == 201
    script_id = created.json()["script_id"]
    return client.post(f"/scripts/{script_id}/execute", json={"reference": {"id": dataset}})


def test_real_python_api_and_audit(env):
    app, a, _ = env
    response = run(a, 'import json; print(json.dumps({"answer": sum(range(10))}))')
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"answer": 45}
    assert response.json()["execution_mode"] == "local_docker"
    assert response.json()["state"] == "COMPLETED"
    with app.state.store.connection() as db:
        event = dict(db.execute("SELECT * FROM execution_audit").fetchone())
    assert event["execution_mode"] == "local_docker"
    assert event["operation"] == "python"
    assert event["tenant_id"] == "demo-tenant-1"


@pytest.mark.parametrize("tenant,dataset,expected", [(1, "railway-demo", 373864), (2, "lufthansa-demo", 395998)])
def test_real_authorized_september(env, tenant, dataset, expected):
    source = (Path(__file__).resolve().parent.parent / "scripts/september.py").read_text()
    response = run(env[tenant], source, dataset)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"month": "September", "ticket_count": 45,
                                           "total_revenue_cents": expected}
    assert len(response.content) < 500


def test_tenant_boundaries_and_source_cannot_select_other_input(env):
    app, a, b = env
    source = ('import json; data=json.load(open("/tmp/input.json")); '
              'print(json.dumps({"tenants":sorted(set(r["tenant_id"] for r in data["records"]))}))')
    assert run(a, source).json()["result"] == {"tenants": ["demo-tenant-1"]}
    assert run(b, source, "lufthansa-demo").json()["result"] == {"tenants": ["demo-tenant-2"]}
    assert run(a, source, "lufthansa-demo").status_code == 404
    script = a.post("/scripts", json={"name": "owned", "source_code": source}).json()
    assert b.post(f"/scripts/{script['script_id']}/execute", json={"reference": {"id": "lufthansa-demo"}}).status_code == 404
    assert b.get(f"/scripts/{script['script_id']}").status_code == 404
    anonymous = TestClient(app)
    assert anonymous.post(f"/scripts/{script['script_id']}/execute", json={"reference": {"id": "railway-demo"}}).status_code == 401


def test_network_destinations_blocked(env):
    source = '''import socket,json
targets=[("1.1.1.1",443),("127.0.0.1",3000),("127.0.0.1",4000),("127.0.0.1",5432),("169.254.169.254",80),("host.docker.internal",4000),("::1",4000)]
results=[]
for host,port in targets:
    try:
        with socket.create_connection((host,port),timeout=0.2): results.append(False)
    except OSError: results.append(True)
print(json.dumps({"blocked":results}))
'''
    response = run(env[1], source)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"blocked": [True] * 7}


def test_actual_anythingmcp_and_postgres_container_ips_blocked(env):
    app = env[0]
    targets = []
    for name, port in (("amcp-app", 4000), ("amcp-postgres", 5432)):
        address = app.state.executor.command("inspect", "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name).decode().strip()
        assert address, "Start the verified AnythingMCP stack for this integration test"
        targets.append((address, port))
    source = 'import socket,json\nblocked=[]\nfor target in ' + repr(targets) + ''':
    try:
        with socket.create_connection(target,timeout=0.2): blocked.append(False)
    except OSError: blocked.append(True)
print(json.dumps({"blocked":blocked}))
'''
    response = run(env[1], source)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"blocked": [True, True]}


def test_filesystem_env_user_namespaces_and_kernel_policy(env):
    source = '''import os,json,pathlib
paths=["/var/run/docker.sock","/run/docker.sock","/app/.env","/data/railway.csv","/data/codebound.sqlite3","/host","D:/work_projects/hackathon/ai_sandbox/anythingmcp/.env"]
denied=[]
for p in ("/etc/codebound-test","/worker/runner.py"):
    try:
        with open(p,"w") as f: f.write("must not write")
        denied.append(False)
    except OSError: denied.append(True)
status=pathlib.Path("/proc/self/status").read_text()
with open("/tmp/scratch","w") as f: f.write("allowed")
print(json.dumps({"host_paths_absent":all(not os.path.exists(p) for p in paths),
"secret_absent":os.environ.get("CODEBOUND_TEST_SECRET") is None and "test-secret-must-never-enter-worker" not in pathlib.Path("/proc/1/environ").read_text(),
"uid":os.getuid(),"writes_denied":denied,"seccomp": "Seccomp:\\t2" in status,
"nnp":"NoNewPrivs:\\t1" in status,"caps":"CapEff:\\t0000000000000000" in status,
"memory_max":pathlib.Path("/sys/fs/cgroup/memory.max").read_text().strip(),
"pids_max":pathlib.Path("/sys/fs/cgroup/pids.max").read_text().strip(),
"cpu_max":pathlib.Path("/sys/fs/cgroup/cpu.max").read_text().strip()}))
'''
    response = run(env[1], source)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    for key in ("host_paths_absent", "secret_absent", "seccomp", "nnp", "caps"):
        assert result[key], result
    assert result["uid"] == 10001
    assert result["writes_denied"] == [True, True]
    assert result["memory_max"] == "134217728"
    assert result["pids_max"] == "16"
    quota, period = map(int, result["cpu_max"].split())
    assert quota / period == 0.5


def test_infinite_loop_timeout_and_cleanup(env):
    start = monotonic()
    response = run(env[1], "while True: pass")
    assert response.status_code == 408, response.text
    assert response.json()["state"] == "TIMED_OUT"
    assert response.json()["error"]["code"] == "TIMED_OUT"
    assert monotonic() - start < 15


def test_outer_deadline_survives_stopped_in_container_supervisor(env):
    # Same-UID untrusted code can stop its in-container parent. The trusted
    # Windows launcher must still terminate the whole container externally.
    source = 'import os,signal\nos.kill(os.getppid(),signal.SIGSTOP)\nwhile True: pass'
    response = run(env[1], source)
    assert response.status_code == 408, response.text
    assert response.json()["state"] == "TIMED_OUT"


def test_memory_exhaustion_is_constrained(env):
    response = run(env[1], 'data=bytearray(512*1024*1024); print("{}")')
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] in {"MEMORY_LIMIT_EXCEEDED", "SCRIPT_FAILED", "WORKER_FAILED"}
    assert response.json()["result"] is None


def test_pid_limit_is_constrained(env):
    source = '''import subprocess,sys,json
children=[]; blocked=False
try:
    for _ in range(40):
        try:
            children.append(subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        except OSError:
            blocked=True; break
finally:
    for p in children: p.kill()
    for p in children: p.wait()
print(json.dumps({"blocked":blocked,"children":len(children)}))
'''
    response = run(env[1], source)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["blocked"] is True
    assert response.json()["result"]["children"] < 16


@pytest.mark.parametrize("source,code,status", [
    ('print("x"*100000)', "OUTPUT_LIMIT_EXCEEDED", 413),
    ('raise RuntimeError("private-input-must-not-leak")', "SCRIPT_FAILED", 422),
    ('print("not JSON")', "INVALID_JSON_RESULT", 422),
    ('invalid python $$', "SCRIPT_FAILED", 422),
])
def test_errors_output_and_cleanup(env, source, code, status):
    response = run(env[1], source)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert "private-input-must-not-leak" not in response.text


def test_ephemeral_scratch_does_not_survive(env):
    assert run(env[1], 'open("/tmp/canary","w").write("one-job"); print("{}")').status_code == 200
    response = run(env[1], 'import os,json; print(json.dumps({"exists":os.path.exists("/tmp/canary")}))')
    assert response.json()["result"] == {"exists": False}


def test_tmpfs_limit(env):
    source = '''import json
blocked=False
try:
    with open("/tmp/fill","wb") as f:
        for _ in range(25): f.write(b"x"*1024*1024)
except OSError: blocked=True
print(json.dumps({"blocked":blocked}))
'''
    response = run(env[1], source)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"blocked": True}


def test_concurrent_capacity_and_tenant_independence(env):
    app, a, b = env
    script = a.post("/scripts", json={"name": "overlap", "source_code": 'import time; time.sleep(1.5); print("{}")'}).json()
    path = f"/scripts/{script['script_id']}/execute"
    body = {"reference": {"id": "railway-demo"}}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(a.post, path, json=body)
        deadline = monotonic() + 5
        while not workers(app):
            assert monotonic() < deadline
            sleep(0.02)
        assert a.post(path, json=body).status_code == 429
        assert run(b, 'print("{}")', "lufthansa-demo").status_code == 200
        assert pending.result(timeout=10).status_code == 200


def test_missing_image_fails_closed(env):
    app, a, _ = env
    app.state.executor = LocalDockerExecutor(app.state.executor.data_dir,
        SandboxSettings(mode="local_docker", worker_image="codebound-worker:does-not-exist"))
    response = run(a, 'print("{}")')
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"
