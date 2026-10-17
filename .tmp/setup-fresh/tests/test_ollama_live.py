"""Opt-in actual local model -> AnythingMCP -> PostgreSQL -> Docker verification."""

import json
import os
import sqlite3
import subprocess
from time import perf_counter
from uuid import uuid4

import pytest

from codebound.configure_mcp import ROOT, STATE
from codebound.connector_broker import tool_value
from codebound.docker_executor import docker_executable, LABEL, PROJECT_LABEL
from codebound.llm.validator import validate_source
from codebound.llm.store import fingerprint
from codebound.llm.client import AIError, OllamaClient, OllamaSettings
from codebound.llm.prompts import prompt
from codebound.llm.service import TYPES
from codebound.mcp_client import McpClient

pytestmark = [pytest.mark.ollama, pytest.mark.skipif(os.getenv("CODEBOUND_TEST_OLLAMA") != "1", reason="Opt in to real local Ollama/MCP/Docker tests")]


class LiveAI:
    def __init__(self, a, b):
        self.a, self.b = a, b
    def __repr__(self):
        return "LiveAI(credentials redacted)"


@pytest.fixture(scope="module")
def clients():
    config = json.loads(STATE.read_text())
    clients = []
    for tenant in ("demo-tenant-1", "demo-tenant-2"):
        entry = config["tenants"][tenant]
        client = McpClient(entry["endpoint"], entry["key"])
        client.initialize()
        names = {tool["name"] for tool in client.tools()}
        assert {"codebound_generate_script", "codebound_modify_script", "codebound_run_task"} <= names
        clients.append(client)
    return LiveAI(*clients)


def last_metrics(script_id, operation="run"):
    with sqlite3.connect(ROOT / "data/codebound.sqlite3") as db:
        row = db.execute("SELECT metrics_json FROM ai_events WHERE script_id=? AND operation=? ORDER BY rowid DESC LIMIT 1",
                         (script_id, operation)).fetchone()
        return json.loads(row[0])


def worker_cleanup():
    result = subprocess.run([docker_executable(), "ps", "-aq", "--filter", "label=" + LABEL,
                             "--filter", "label=" + PROJECT_LABEL], capture_output=True, check=True)
    assert not result.stdout.strip()


def test_real_generation_reuse_modification_and_execution(clients):
    a, b = clients.a, clients.b
    marker = uuid4().hex[:8]
    task = f"Calculate September 2026 ticket revenue. Verification label {marker}."
    args = {"task": task, "reference": {"kind": "connector", "id": "postgres-railway-a"},
            "parameters": {"year": 2026, "month": 9}}
    sid = None
    timings = {}
    try:
        start = perf_counter()
        first = tool_value(a.call("codebound_run_task", args))
        first_mcp_body_bytes = a.last_response_bytes
        timings["first_wall_ms"] = round((perf_counter() - start) * 1000, 3)
        sid = first["script_id"]
        assert first["ollama_called"] is True and first["script_reused"] is False
        execution = first["execution"]
        assert execution["status"] == "succeeded" and execution["execution_mode"] == "local_docker"
        result = execution["result"]
        assert result["ticket_count"] == 45 and result["total_revenue_cents"] == 373864
        script = tool_value(a.call("codebound_get_script", {"script_id": sid}))
        validate_source(script["source_code"])
        first_metrics = last_metrics(sid)
        assert first_metrics["ollama_calls"] == 1 and first_metrics["eval_count"] > 0
        assert first_metrics["input_json_bytes"] == 80409
        start = perf_counter()
        second = tool_value(a.call("codebound_run_task", args))
        timings["reuse_wall_ms"] = round((perf_counter() - start) * 1000, 3)
        reuse_metrics = last_metrics(sid)
        assert second["script_id"] == sid and second["script_reused"] is True and second["ollama_called"] is False
        assert reuse_metrics["ollama_calls"] == 0 and "eval_count" not in reuse_metrics
        assert second["execution"]["result"] == result
        assert b.call("codebound_modify_script", {"script_id": sid, "instruction": "Show average revenue"}).get("isError")
        assert b.call("codebound_run_task", args).get("isError")
        modified = tool_value(a.call("codebound_modify_script", {"script_id": sid,
            "instruction": "Also calculate the average revenue per selected ticket in average_revenue_cents. Preserve existing counts, totals, date and ticket-type filters.",
            "expected_version": first["version"]}))
        assert modified["version"] == 2 and modified["ollama_called"] is True
        updated = tool_value(a.call("codebound_get_script", {"script_id": sid}))
        assert fingerprint(updated["source_code"]) != fingerprint(script["source_code"])
        validate_source(updated["source_code"])
        output = tool_value(a.call("codebound_execute_script", {"script_id": sid,
            "reference": args["reference"], "parameters": args["parameters"]}))
        assert output["execution_mode"] == "local_docker" and output["status"] == "succeeded"
        assert output["result"]["ticket_count"] == 45 and output["result"]["total_revenue_cents"] == 373864
        assert output["result"]["average_revenue_cents"] == pytest.approx(373864 / 45, abs=.01)
        assert len(json.dumps(first).encode()) < 2048
        evidence = {"task_result": result, "modified_result": output["result"], "first_metrics": first_metrics,
                    "reuse_metrics": reuse_metrics, "modify_metrics": last_metrics(sid, "modify"), "timings": timings,
                    "first_response_json_bytes": len(json.dumps(first, separators=(",", ":")).encode()),
                    "first_mcp_http_response_body_bytes": first_mcp_body_bytes, "run_task_reuse_rate": .5}
        (ROOT / ".local/ollama-demo-evidence.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence))
    finally:
        if sid:
            a.call("codebound_delete_script", {"script_id": sid})
        worker_cleanup()


def test_generate_only_does_not_execute_and_tenant_b_owns_script(clients):
    b = clients.b
    sid = None
    try:
        response = tool_value(b.call("codebound_generate_script", {"task": "Calculate September 2026 ticket revenue.",
            "reference": {"kind": "connector", "id": "postgres-railway-b"}, "parameters": {"year": 2026}}))
        sid = response["script_id"]
        script = tool_value(b.call("codebound_get_script", {"script_id": sid}))
        assert script["tenant_id"] == "demo-tenant-2"
        validate_source(script["source_code"])
        with sqlite3.connect(ROOT / "data/codebound.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM execution_audit WHERE script_id=?", (sid,)).fetchone()[0] == 0
        assert clients.a.call("codebound_modify_script", {"script_id": sid, "instruction": "Change"}).get("isError")
    finally:
        if sid:
            b.call("codebound_delete_script", {"script_id": sid})
        worker_cleanup()


def test_bad_dataset_never_generates_or_executes(clients):
    with sqlite3.connect(ROOT / "data/codebound.sqlite3") as db:
        before = db.execute("SELECT count(*) FROM execution_audit").fetchone()[0]
    assert clients.a.call("codebound_run_task", {"task": "Revenue", "reference": {"kind": "connector", "id": "postgres-railway-b"}}).get("isError")
    with sqlite3.connect(ROOT / "data/codebound.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM execution_audit").fetchone()[0] == before
        row = db.execute("SELECT metrics_json FROM ai_events WHERE operation='run' ORDER BY rowid DESC LIMIT 1").fetchone()
        assert json.loads(row[0])["ollama_calls"] == 0
    worker_cleanup()


def test_real_missing_model_fails_without_fallback():
    client = OllamaClient(OllamaSettings(model="codebound-missing-" + uuid4().hex))
    with pytest.raises(AIError, match="LOCAL_MODEL_UNAVAILABLE"):
        client.ready()


def test_real_generation_deadline_closes_local_request():
    client = OllamaClient(OllamaSettings(model=os.environ["OLLAMA_MODEL"], generation_timeout=1))
    metrics = {}
    with pytest.raises(AIError, match="OLLAMA_TIMEOUT"):
        client.generate(prompt("Calculate ticket revenue and average revenue per ticket.",
            {"contract_version": 1, "columns": TYPES}, {"year": 2026, "month": 9, "ticket_type": None}), metrics)
    assert metrics["ollama_calls"] == 1
    worker_cleanup()
