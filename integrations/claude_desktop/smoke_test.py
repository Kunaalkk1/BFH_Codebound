"""Real SDK stdio client test; Claude Desktop is not required or impersonated."""

import asyncio
import json
import logging
from pathlib import Path
import sys
from uuid import uuid4

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from bridge import TOOLS, upstream
from credentials import Credential, MANIFEST, TENANT

HERE = Path(__file__).resolve().parent


def value(result):
    if result.is_error:
        raise AssertionError("Unexpected upstream tool error")
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def smoke():
    parameters = StdioServerParameters(command=sys.executable, args=["-u", str(HERE / "bridge.py")], cwd=str(HERE))
    # Use the SDK, including stdio framing, initialization, transport negotiation,
    # typed tool discovery and tool calls. No handwritten protocol assertions.
    async with Client(parameters, mode="legacy", read_timeout_seconds=60, cache=None) as bridge:
        listed = await bridge.list_tools()
        assert {tool.name for tool in listed.tools} == TOOLS
        print("PASS SDK stdio initialization and discovery: 10 Codebound tools")
        datasets = value(await bridge.call_tool("codebound_list_datasets", {}))["datasets"]
        ids = {dataset["id"] for dataset in datasets}
        assert {"railway-demo", "postgres-railway-a"} <= ids
        assert not {"lufthansa-demo", "postgres-railway-b"} & ids
        print("PASS actual authenticated dataset call; tenant B datasets absent")
        assert (await bridge.call_tool("codebound_data_b", {})).is_error
        assert (await bridge.call_tool("codebound_get_script", {"script_id": "invalid-uuid"})).is_error
        assert (await bridge.call_tool("codebound_create_script", {"name": "Denied", "source_code": "x", "tenant_id": "demo-tenant-2"})).is_error
        print("PASS private tool, invalid input and tenant argument rejected")
        marker = "Bridge smoke " + uuid4().hex
        own = value(await bridge.call_tool("codebound_create_script", {"name": marker, "source_code": "# disposable bridge smoke; never execute"}))
        own_id = own["script_id"]
        try:
            assert own["tenant_id"] == TENANT
            assert value(await bridge.call_tool("codebound_get_script", {"script_id": own_id}))["name"] == marker
            assert (await bridge.call_tool("codebound_execute_script", {"script_id": own_id,
                "reference": {"kind": "connector", "id": "postgres-railway-b"}})).is_error
            # Fixture creation through tenant B's own authenticated gateway endpoint.
            # B's key is used ONLY by this test, never by the stdio bridge.
            entry = json.loads(MANIFEST.read_text())["tenants"]["demo-tenant-2"]
            credential_b = Credential(entry["endpoint"], entry["key"])
            async with upstream(credential_b) as b:
                other = value(await b.call_tool("codebound_create_script", {"name": marker, "source_code": "# B-owned disposable fixture"}))
                other_id = other["script_id"]
                try:
                    assert other["tenant_id"] == "demo-tenant-2"
                    for operation, args in [
                        ("get_script", {}), ("update_script", {"name": "Denied", "source_code": "x"}),
                        ("delete_script", {}), ("execute_script", {"reference": {"kind": "connector", "id": "postgres-railway-a"}}),
                    ]:
                        assert (await bridge.call_tool("codebound_" + operation, {"script_id": other_id, **args})).is_error
                    assert value(await b.call_tool("codebound_get_script", {"script_id": other_id}))["name"] == marker
                finally:
                    assert not (await b.call_tool("codebound_delete_script", {"script_id": other_id})).is_error
            print("PASS tenant A cannot read/update/delete/execute B-owned script or select B connector")
        finally:
            assert not (await bridge.call_tool("codebound_delete_script", {"script_id": own_id})).is_error
        assert (await bridge.call_tool("codebound_get_script", {"script_id": own_id})).is_error
        assert {tool.name for tool in (await bridge.list_tools()).tools} == TOOLS
        print("PASS disposable cleanup and successful request after errors")
    print("BRIDGE SMOKE PASSED; real Claude Desktop invocation still requires verification in the app")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(smoke())
    except Exception:
        print("BRIDGE SMOKE FAILED; check local service status and bridge credential setup", file=sys.stderr)
        raise SystemExit(1)
