"""Provision a separate synthetic database and private AnythingMCP data server.

Trusted operator command only; never exposed to MCP or an execution worker.
"""

import csv
import json
import secrets
import subprocess

from codebound.configure_mcp import Management, ROOT, STATE, save
from codebound.docker_executor import docker_executable
from codebound.mcp_client import IntegrationError


def sql(statement, database="postgres"):
    result = subprocess.run([docker_executable(), "exec", "-i", "amcp-postgres", "psql",
                             "-U", "amcp", "-d", database, "-v", "ON_ERROR_STOP=1", "-At"],
                            input=statement.encode(), capture_output=True, timeout=30)
    if result.returncode:
        raise IntegrationError("Synthetic database provisioning failed (details suppressed)")
    return result.stdout.decode().strip()


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def configure():
    api = Management()
    state = json.loads(STATE.read_text())
    state.setdefault("database_passwords", {"a": secrets.token_hex(32), "b": secrets.token_hex(32)})
    save(state)
    if sql("SELECT count(*) FROM pg_database WHERE datname='codebound_railway_demo'") == "0":
        sql("CREATE DATABASE codebound_railway_demo;")
    sql("REVOKE ALL ON DATABASE codebound_railway_demo FROM PUBLIC;")
    columns = ["ticket_id", "tenant_id", "travel_date", "ticket_type", "price_cents"]
    if sql("SELECT to_regclass('public.tickets') IS NULL", "codebound_railway_demo") == "t":
        with (ROOT / "data/railway.csv").open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        values = ",\n".join("(" + ",".join(quote(row[column]) for column in columns) + ")" for row in rows)
        sql("BEGIN; CREATE TABLE tickets (ticket_id text PRIMARY KEY, tenant_id text NOT NULL, "
            "travel_date text NOT NULL, ticket_type text NOT NULL, price_cents integer NOT NULL); "
            "INSERT INTO tickets VALUES " + values + "; COMMIT;", "codebound_railway_demo")
    for suffix, tenant in (("a", "demo-tenant-1"), ("b", "demo-tenant-2")):
        role = "codebound_reader_" + suffix
        if sql("SELECT count(*) FROM pg_roles WHERE rolname=" + quote(role)) == "0":
            sql("CREATE ROLE " + role + " LOGIN PASSWORD " + quote(state["database_passwords"][suffix]) + ";")
        sql(f"GRANT CONNECT ON DATABASE codebound_railway_demo TO {role};")
        sql(f"CREATE OR REPLACE VIEW tickets_{suffix} WITH (security_barrier=true) AS "
            f"SELECT * FROM tickets WHERE tenant_id={quote(tenant)}; "
            f"GRANT USAGE ON SCHEMA public TO {role}; GRANT SELECT ON tickets_{suffix} TO {role}; "
            f"ALTER ROLE {role} SET default_transaction_read_only=on;", "codebound_railway_demo")
    policy = api.request("GET", "admin/settings/ssrf-allowed-hosts")
    if "postgres" not in policy["hosts"]:
        api.request("PUT", "admin/settings/ssrf-allowed-hosts", {"hosts": [*policy["hosts"], "postgres"]})
    data = state.setdefault("data_server", {})
    connectors = data.setdefault("connectors", {})
    for suffix in ("a", "b"):
        if suffix not in connectors:
            connector = api.request("POST", "connectors", {"name": "Codebound PostgreSQL " + suffix.upper(),
                "type": "DATABASE", "baseUrl": "postgresql://postgres:5432/codebound_railway_demo",
                "authType": "CONNECTION_STRING", "authConfig": {"username": "codebound_reader_" + suffix,
                "password": state["database_passwords"][suffix]}})
            connectors[suffix] = connector["id"]
            save(state)
        connector_id = connectors[suffix]
        existing = api.request("GET", "connectors/" + connector_id)["tools"]
        name = "codebound_data_" + suffix
        if name not in {item["name"] for item in existing}:
            api.request("POST", f"connectors/{connector_id}/tools", {"name": name,
                "description": "Private broker: authorized synthetic tickets only",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                "endpointMapping": {"method": "query", "path": f"SELECT {','.join(columns)} FROM tickets_{suffix} ORDER BY ticket_id"}})
        schema_name = "codebound_schema_" + suffix
        if schema_name not in {item["name"] for item in existing}:
            api.request("POST", f"connectors/{connector_id}/tools", {"name": schema_name,
                "description": "Private broker: authorized view column names and SQL types only",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                "endpointMapping": {"method": "query", "path": "SELECT column_name,data_type FROM information_schema.columns "
                    f"WHERE table_schema='public' AND table_name='tickets_{suffix}' ORDER BY ordinal_position"}})
    if "server_id" not in data:
        server = api.request("POST", "mcp-servers", {"name": "Codebound private data broker"})
        data["server_id"] = server["id"]
        save(state)
    # Refuse to replace an assignment that an operator has changed.
    detail = api.request("GET", "mcp-servers/" + data["server_id"])
    assigned = {item["connector"]["id"] for item in detail.get("connectors", [])}
    if assigned and assigned != set(connectors.values()):
        raise IntegrationError("Private broker assignments changed; review before retrying")
    api.request("PUT", f"mcp-servers/{data['server_id']}/connectors", {"connectorIds": list(connectors.values())})
    if "key" not in data:
        key = api.request("POST", "mcp-keys", {"name": "Codebound private data broker", "mcpServerId": data["server_id"]})
        data["key"], data["key_id"] = key["key"], key["id"]
        save(state)
    state["broker"] = {tenant: {"endpoint": "http://127.0.0.1:4000/mcp/" + data["server_id"],
        "key": data["key"], "reference": "postgres-railway-" + suffix, "tool": "codebound_data_" + suffix,
        "schema_tool": "codebound_schema_" + suffix}
        for suffix, tenant in (("a", "demo-tenant-1"), ("b", "demo-tenant-2"))}
    save(state)
    print("Private PostgreSQL data broker configured; restart only the Codebound backend to load it.")


if __name__ == "__main__":
    try:
        configure()
    except (IntegrationError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(str(exc) if isinstance(exc, IntegrationError) else "Connector provisioning failed; no credentials displayed")
        raise SystemExit(1)
