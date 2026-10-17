"""Opt-in live tests: real AnythingMCP routing and real disposable Docker jobs."""

import json
import os
import sqlite3
import subprocess

import pytest

from codebound.configure_mcp import ROOT, STATE
from codebound.connector_broker import ConnectorBroker, tool_value
from codebound.docker_executor import docker_executable, LABEL, PROJECT_LABEL
from codebound.mcp_client import IntegrationError, McpClient

pytestmark = [pytest.mark.mcp, pytest.mark.skipif(os.getenv("CODEBOUND_TEST_MCP") != "1", reason="Opt in to live MCP verification")]


class LiveContext:
    def __init__(self, clients, configuration):
        self.clients, self.configuration = clients, configuration
    def __iter__(self):
        return iter((self.clients, self.configuration))
    def __getitem__(self, index):
        return (self.clients, self.configuration)[index]
    def __repr__(self):
        return "LiveContext(credentials redacted)"


@pytest.fixture(scope="module")
def clients():
    config = json.loads(STATE.read_text())
    result = {}
    for tenant, entry in config["tenants"].items():
        client = McpClient(entry["endpoint"], entry["key"])
        assert client.initialize()["protocolVersion"] == "2025-06-18"
        result[tenant] = client
    return LiveContext(result, config)


def test_discovery_and_authentication(clients):
    instances, config = clients
    required = {"codebound_" + name for name in ["list_datasets", "list_scripts", "create_script", "get_script", "update_script", "delete_script", "execute_script"]}
    for client in instances.values():
        names = {tool["name"] for tool in client.tools()}
        assert required <= names
        assert not {"codebound_data_a", "codebound_data_b"} & names
        for private_tool in ("codebound_data_a", "codebound_data_b"):
            with pytest.raises(IntegrationError, match="MCP request failed"):
                client.call(private_tool, {})
        with pytest.raises(IntegrationError, match="401"):
            McpClient(client.endpoint, "invalid").initialize()
    entries = list(config["tenants"].values())
    with pytest.raises(IntegrationError):
        McpClient(entries[1]["endpoint"], entries[0]["key"]).initialize()
    private_endpoint = config["broker"]["demo-tenant-1"]["endpoint"]
    with pytest.raises(IntegrationError):
        McpClient(private_endpoint, entries[0]["key"]).initialize()


def test_crud_connector_execution_and_tenant_isolation(clients):
    instances, config = clients
    a, b = instances["demo-tenant-1"], instances["demo-tenant-2"]
    source = (ROOT / "scripts/september.py").read_text()
    record = tool_value(a.call("codebound_create_script", {"name": "MCP verification", "source_code": source}))
    sid = record["script_id"]
    try:
        assert record["tenant_id"] == "demo-tenant-1" and record["version"] == 1
        assert tool_value(a.call("codebound_get_script", {"script_id": sid}))["script_id"] == sid
        assert sid in {r["script_id"] for r in tool_value(a.call("codebound_list_scripts", {}))}
        updated = tool_value(a.call("codebound_update_script", {"script_id": sid, "name": "MCP updated", "source_code": source + "\n# MCP version 2\n"}))
        assert updated["version"] == 2
        for operation, args in [("get_script", {}), ("update_script", {"name": "denied", "source_code": source}), ("delete_script", {}),
                                ("execute_script", {"reference": {"kind": "connector", "id": "postgres-railway-a"}})]:
            assert b.call("codebound_" + operation, {"script_id": sid, **args}).get("isError")
        assert a.call("codebound_execute_script", {"script_id": sid, "reference": {"kind": "connector", "id": "postgres-railway-b"}}).get("isError")
        # REST mappings discard unrecognized arguments; they cannot select identity.
        attempted = tool_value(a.call("codebound_create_script", {"name": "identity check", "source_code": "x", "tenant_id": "demo-tenant-2"}))
        assert attempted["tenant_id"] == "demo-tenant-1"
        a.call("codebound_delete_script", {"script_id": attempted["script_id"]})
        assert a.call("codebound_create_script", {"name": "invalid", "source_code": ""}).get("isError")
        response = tool_value(a.call("codebound_execute_script", {"script_id": sid, "reference": {"kind": "connector", "id": "postgres-railway-a"}, "parameters": {"year": 2026}}))
        assert response["execution_mode"] == "local_docker" and response["status"] == "succeeded"
        assert response["result"] == {"month": "September", "ticket_count": 45, "total_revenue_cents": 373864}
        print("Authenticated MCP result: " + json.dumps(response, separators=(",", ":")))
        print("Tenant B read/update/delete/execute of tenant A script: DENIED; cross-tenant connector: DENIED")
        rows = ConnectorBroker(config["broker"], 1_000_000).fetch("demo-tenant-1", "postgres-railway-a")
        assert len(rows) == 600
        raw_size, result_size = len(json.dumps(rows).encode()), len(json.dumps(response).encode())
        assert result_size < 1024 and raw_size > result_size * 100
        print(f"PostgreSQL tenant A: {len(rows)} rows, {raw_size} raw bytes, {result_size} execution JSON bytes")
        with sqlite3.connect(ROOT / "data/codebound.sqlite3") as db:
            audit = db.execute("SELECT status, execution_mode FROM execution_audit WHERE execution_id=?", (response["execution_id"],)).fetchone()
            assert audit == ("succeeded", "local_docker")
    finally:
        a.call("codebound_delete_script", {"script_id": sid})
    assert a.call("codebound_get_script", {"script_id": sid}).get("isError")


@pytest.mark.parametrize("source", ["while True: pass", "raise RuntimeError('private failure detail')"])
def test_worker_errors_and_cleanup(clients, source):
    a = clients[0]["demo-tenant-1"]
    script = tool_value(a.call("codebound_create_script", {"name": "MCP failure check", "source_code": source}))
    try:
        response = a.call("codebound_execute_script", {"script_id": script["script_id"], "reference": {"kind": "connector", "id": "postgres-railway-a"}})
        assert response.get("isError")
        assert "private failure detail" not in json.dumps(response)
    finally:
        a.call("codebound_delete_script", {"script_id": script["script_id"]})
    result = subprocess.run([docker_executable(), "ps", "-aq", "--filter", "label=" + LABEL, "--filter", "label=" + PROJECT_LABEL], capture_output=True, check=True)
    assert not result.stdout.strip()


def test_tenant_b_connector_and_worker_has_no_credentials(clients):
    b = clients[0]["demo-tenant-2"]
    source = (ROOT / "scripts/september.py").read_text()
    record = tool_value(b.call("codebound_create_script", {"name": "Tenant B live verification", "source_code": source}))
    try:
        result = tool_value(b.call("codebound_execute_script", {"script_id": record["script_id"], "reference": {"kind": "connector", "id": "postgres-railway-b"}}))
        assert result["result"]["ticket_count"] == 45
        assert result["result"]["total_revenue_cents"] == 395998
        probe = "import json, os\nd=json.load(open('/tmp/input.json'))\nprint(json.dumps({'input_keys':sorted(d), 'record_keys':sorted(d['records'][0]), 'env_keys':sorted(os.environ)}))"
        tool_value(b.call("codebound_update_script", {"script_id": record["script_id"], "name": "Worker input probe", "source_code": probe}))
        result = tool_value(b.call("codebound_execute_script", {"script_id": record["script_id"], "reference": {"kind": "connector", "id": "postgres-railway-b"}}))["result"]
        assert result["input_keys"] == ["parameters", "records"]
        assert result["record_keys"] == ["price_cents", "tenant_id", "ticket_id", "ticket_type", "travel_date"]
        assert not any(any(secret in key.lower() for secret in ["token", "password", "secret", "credential"]) for key in result["env_keys"])
    finally:
        b.call("codebound_delete_script", {"script_id": record["script_id"]})


def test_postgresql_roles_cannot_read_other_tenant():
    from codebound.configure_connector import sql
    result = sql("SELECT has_table_privilege('codebound_reader_a','tickets_a','SELECT'), "
                 "has_table_privilege('codebound_reader_a','tickets_b','SELECT'), "
                 "has_table_privilege('codebound_reader_b','tickets_a','SELECT'), "
                 "has_table_privilege('codebound_reader_b','tickets_b','SELECT'), "
                 "has_table_privilege('codebound_reader_a','tickets','SELECT')", "codebound_railway_demo")
    assert result == "t|f|f|t|f"


def test_gateway_records_project_connector_invocations(clients):
    from codebound.configure_connector import sql, quote
    configuration = clients.configuration
    ids = [entry["connector_id"] for entry in configuration["tenants"].values()]
    ids += list(configuration["data_server"]["connectors"].values())
    for connector_id in ids:
        count = sql("SELECT count(*) FROM tool_invocations WHERE connector_id=" + quote(connector_id)
                    + " AND status='SUCCESS'", "anythingmcp")
        assert int(count) > 0
