"""Typed MCP tools proxy: SDK stdio server -> SDK authenticated Streamable HTTP.

No custom JSON-RPC framing, source execution, credential arguments or tenant tools.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
from importlib.metadata import version
import sys

import anyio
import httpx2
from jsonschema import Draft202012Validator
from referencing import Registry
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
import mcp_types as types

from credentials import CredentialError, load, prepare

TOOLS = frozenset("codebound_" + name for name in (
    "list_datasets", "list_scripts", "create_script", "get_script", "update_script",
    "delete_script", "execute_script", "generate_script", "modify_script", "run_task"))


def diagnostic(message):
    print("Codebound bridge: " + message, file=sys.stderr, flush=True)


@asynccontextmanager
async def upstream(credential):
    async def exact_endpoint(request):
        # SDK redirect handling must not forward credentials to another URL/path.
        if str(request.url) != credential.endpoint:
            raise ValueError("Unexpected upstream destination")
    async with httpx2.AsyncClient(headers={"X-API-Key": credential.key}, trust_env=False,
            follow_redirects=False, timeout=httpx2.Timeout(45, connect=3),
            event_hooks={"request": [exact_endpoint]}) as http:
        transport = streamable_http_client(credential.endpoint, http_client=http, max_sse_event_size=2_097_152)
        # The SDK Client owns the transport context and its session lifecycle.
        # This installed AnythingMCP uses the initialize-handshake protocol era.
        async with Client(transport, mode="legacy", read_timeout_seconds=45, cache=None) as client:
            yield client


async def catalog(client):
    tools, cursor, seen = {}, None, set()
    for _ in range(10):
        page = await client.list_tools(cursor=cursor)
        for tool in page.tools:
            if tool.name in TOOLS:
                if tool.name in tools:
                    raise ValueError("Duplicate upstream tool")
                # The installed gateway simplifies some JSON schemas and omits
                # additionalProperties. Explicitly close each tool's argument object.
                tools[tool.name] = tool.model_copy(update={"input_schema": {**tool.input_schema, "additionalProperties": False}})
        cursor = page.next_cursor
        if not cursor:
            break
        if cursor in seen:
            raise ValueError("Repeated upstream cursor")
        seen.add(cursor)
    else:
        raise ValueError("Upstream pagination limit")
    if set(tools) != TOOLS:
        raise ValueError("Incomplete upstream tool catalog")
    return tools


def build_server(credential):
    async def list_tools(ctx, params):
        try:
            with anyio.fail_after(50):
                async with upstream(credential) as client:
                    tools = await catalog(client)
                    return types.ListToolsResult(tools=list(tools.values()))
        except Exception:
            diagnostic("authenticated discovery failed; check local services and credential configuration")
            raise MCPError(types.ErrorData(code=-32603, message="Codebound upstream unavailable")) from None

    async def call_tool(ctx, params):
        if params.name not in TOOLS or params.task or params.input_responses or params.request_state:
            return failure("Tool or request mode not supported")
        try:
            with anyio.fail_after(50):
                # Fresh SDK session per request: service restarts do not require retries
                # of effectful operations or leave a stale long-lived upstream session.
                async with upstream(credential) as client:
                    tools = await catalog(client)
                    validator = Draft202012Validator(tools[params.name].input_schema, registry=Registry())
                    if not validator.is_valid(params.arguments or {}):
                        return failure("Invalid Codebound tool arguments")
                    result = await client.call_tool(params.name, params.arguments or {})
                    if result.is_error:
                        return failure("Codebound request denied or failed; check authorized inputs and local service status")
                    return result
        except Exception:
            diagnostic("tool request failed; no automatic retry performed")
            return failure("Codebound upstream request failed")

    return Server("codebound-tenant-a", version="1.0.0", on_list_tools=list_tools, on_call_tool=call_tool,
                  instructions="Tenant A Codebound tools via AnythingMCP. Credentials and tenant are fixed locally.")


def failure(message):
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


async def serve():
    credential = load()
    server = build_server(credential)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    # Library tracebacks can contain remote response bodies. Only fixed diagnostics
    # below go to stderr; stdout belongs exclusively to the SDK stdio transport.
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--prepare-credentials", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if version("mcp") != "2.3.0":
            raise CredentialError("Unsupported SDK version")
        if args.prepare_credentials:
            prepare()
            diagnostic("tenant A credential protected with current-user Windows DPAPI")
        elif args.check:
            load()
            diagnostic("dependencies and protected tenant A credential ready")
        else:
            asyncio.run(serve())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        diagnostic("startup failed; run launch/configure-claude.ps1 and check local services")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
