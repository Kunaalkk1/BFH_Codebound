"""Register only project-owned REST connectors using a user-provided admin login.

Run with python -m codebound.configure_mcp. Progress is journaled after each
creation so a retry never replaces an unrelated connector or server assignment.
"""

import json
from pathlib import Path

from codebound.mcp_client import IntegrationError, local_request
from codebound.mcp_tools import tools, ai_tools

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".local/anythingmcp-integration.json"


class Management:
    def __init__(self):
        try:
            self.token = (ROOT / ".local/anythingmcp-admin-token.txt").read_text(encoding="utf-8-sig").strip()
        except OSError:
            raise IntegrationError("Run launch/login-anythingmcp.ps1 first") from None

    def request(self, method, path, body=None):
        raw, _ = local_request("http://127.0.0.1:4000/api/" + path, method=method, body=body,
                               headers={"Authorization": "Bearer " + self.token})
        return json.loads(raw) if raw else None


def save(state):
    # The directory is gitignored; use atomic replacement to avoid partial JSON.
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(STATE)


def configure(*, ollama=False):
    api = Management()
    if api.request("GET", "users/me").get("role") != "ADMIN":
        raise IntegrationError("An existing local administrator login is required")
    tokens = json.loads((ROOT / ".local/demo-tokens.json").read_text(encoding="utf-8-sig"))
    state = json.loads(STATE.read_text()) if STATE.exists() else {"tenants": {}}
    # Permit just the development API hostname, preserving all existing entries.
    policy = api.request("GET", "admin/settings/ssrf-allowed-hosts")
    if "host.docker.internal" not in policy["hosts"]:
        api.request("PUT", "admin/settings/ssrf-allowed-hosts",
                    {"hosts": [*policy["hosts"], "host.docker.internal"]})
    for suffix, tenant in (("a", "demo-tenant-1"), ("b", "demo-tenant-2")):
        entry = state["tenants"].setdefault(tenant, {})
        if "connector_id" not in entry:
            connector = api.request("POST", "connectors", {"name": "Codebound local " + suffix.upper(),
                "type": "REST", "baseUrl": "http://host.docker.internal:8000", "authType": "BEARER_TOKEN",
                "authConfig": {"token": tokens[suffix]}})
            entry["connector_id"] = connector["id"]
            save(state)
        connector_id = entry["connector_id"]
        existing = api.request("GET", "connectors/" + connector_id)["tools"]
        for tool in tools() + (ai_tools() if ollama else []):
            previous = next((item for item in existing if item["name"] == tool["name"]), None)
            if previous is None:
                api.request("POST", f"connectors/{connector_id}/tools", tool)
            elif any(previous.get(field) != value for field, value in tool.items()):
                api.request("PUT", f"connectors/{connector_id}/tools/{previous['id']}", tool)
        if "server_id" not in entry:
            server = api.request("POST", "mcp-servers", {"name": "Codebound local " + suffix.upper()})
            entry["server_id"] = server["id"]
            save(state)
            api.request("PUT", f"mcp-servers/{server['id']}/connectors", {"connectorIds": [connector_id]})
        else:
            detail = api.request("GET", "mcp-servers/" + entry["server_id"])
            # Existing assignments are never silently replaced on a retry.
            assigned = {item["connector"]["id"] for item in detail.get("connectors", [])}
            if not assigned:
                api.request("PUT", f"mcp-servers/{entry['server_id']}/connectors", {"connectorIds": [connector_id]})
            elif assigned != {connector_id}:
                raise IntegrationError("Project MCP server assignments changed; review them before retrying")
            if not detail:
                raise IntegrationError("Project server is missing")
        if "key" not in entry:
            key = api.request("POST", "mcp-keys", {"name": "Codebound local " + suffix.upper(),
                              "mcpServerId": entry["server_id"]})
            entry["key"], entry["key_id"] = key["key"], key["id"]
            save(state)
        entry["endpoint"] = "http://127.0.0.1:4000/mcp/" + entry["server_id"]
        if ollama:
            entry["ai_enabled"] = True
        save(state)
        print("Registered tenant " + suffix.upper() + ": " + entry["endpoint"])


if __name__ == "__main__":
    try:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--ollama", action="store_true")
        configure(ollama=parser.parse_args().ollama)
    except (IntegrationError, OSError, ValueError, KeyError) as exc:
        # Never print remote bodies, credentials, or tracebacks.
        print(str(exc) if isinstance(exc, IntegrationError) else "Integration configuration failed; check local setup")
        raise SystemExit(1)
