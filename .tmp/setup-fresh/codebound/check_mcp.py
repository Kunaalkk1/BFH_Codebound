"""Credential-safe discovery check; execution demo is the live acceptance suite."""

import json
from codebound.configure_mcp import STATE
from codebound.mcp_client import IntegrationError, McpClient
from codebound.mcp_tools import tools, ai_tools


def check():
    if not STATE.exists():
        raise IntegrationError("MCP integration NOT CONFIGURED; run launch/integrate.ps1 -Configure")
    state = json.loads(STATE.read_text())
    for tenant, entry in state["tenants"].items():
        required = {tool["name"] for tool in tools() + (ai_tools() if entry.get("ai_enabled") else [])}
        client = McpClient(entry["endpoint"], entry["key"])
        client.initialize()
        if not required.issubset({tool["name"] for tool in client.tools()}):
            raise IntegrationError("Codebound MCP tool discovery incomplete")
        print(tenant + f": {len(required)} Codebound tools discovered at " + entry["endpoint"])
    if len(state.get("broker", {})) != 2:
        raise IntegrationError("PostgreSQL broker NOT CONFIGURED")


if __name__ == "__main__":
    try:
        check()
    except (IntegrationError, OSError, ValueError, KeyError) as exc:
        print(str(exc) if isinstance(exc, IntegrationError) else "MCP configuration check failed")
        raise SystemExit(1)
