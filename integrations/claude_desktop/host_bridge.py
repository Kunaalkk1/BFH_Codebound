"""SDK stdio proxy for the tools visible to your own host MCP key."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import sys

import anyio
import httpx2
from jsonschema import Draft202012Validator
from referencing import Registry
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp_types as types

from host_credentials import load

DEMO_TOOLS=frozenset({'price_drift_check','catalogue_total','stuck_orders','mock_supplier_list_offers','codebound_run_task'})


@asynccontextmanager
async def upstream(credential):
    async def exact_destination(request):
        if str(request.url) != credential.endpoint:
            raise ValueError('Unexpected upstream destination')
    async with httpx2.AsyncClient(headers={'X-API-Key': credential.key}, trust_env=False,
            follow_redirects=False, timeout=httpx2.Timeout(45, connect=3),
            event_hooks={'request': [exact_destination]}) as http:
        transport = streamable_http_client(credential.endpoint, http_client=http, max_sse_event_size=2_097_152)
        async with Client(transport, mode='legacy', read_timeout_seconds=45, cache=None) as client:
            yield client


async def catalog(client):
    tools, cursor, seen = {}, None, set()
    for _ in range(10):
        page = await client.list_tools(cursor=cursor)
        for tool in page.tools:
            if tool.name in tools or len(tools) >= 200:
                raise ValueError('Duplicate or oversized MCP catalog')
            tools[tool.name] = tool
        cursor = page.next_cursor
        if not cursor:
            return tools
        if cursor in seen:
            raise ValueError('Repeated catalog cursor')
        seen.add(cursor)
    raise ValueError('Catalog pagination limit')


def failure(message):
    return types.CallToolResult(content=[types.TextContent(type='text', text=message)], is_error=True)


def build_server(credential, *, demo=False):
    async def visible(client):
        tools=await catalog(client)
        if demo:
            if not DEMO_TOOLS <= tools.keys(): raise ValueError('Demo tools incomplete')
            return {name:tools[name] for name in sorted(DEMO_TOOLS)}
        return tools
    async def list_tools(ctx, params):
        try:
            with anyio.fail_after(50):
                async with upstream(credential) as client:
                    return types.ListToolsResult(tools=list((await visible(client)).values()))
        except Exception:
            from mcp.shared.exceptions import MCPError
            raise MCPError(types.ErrorData(code=-32603,
                message='Host MCP unavailable; check services and your key')) from None

    async def call_tool(ctx, params):
        if params.task or params.input_responses or params.request_state:
            return failure('Request mode unsupported')
        try:
            with anyio.fail_after(50):
                async with upstream(credential) as client:
                    tools = await visible(client)
                    if params.name not in tools:
                        return failure('Tool not available for this account')
                    validator = Draft202012Validator(tools[params.name].input_schema, registry=Registry())
                    if not validator.is_valid(params.arguments or {}):
                        return failure('Invalid tool arguments')
                    result = await client.call_tool(params.name, params.arguments or {})
                    return failure('Tool request denied or failed') if result.is_error else result
        except Exception:
            return failure('Host MCP request failed; no automatic retry was performed')

    return Server('codebound-host', version='1.0.0', on_list_tools=list_tools, on_call_tool=call_tool,
        instructions='Tools from your configured AnythingMCP host. The gateway enforces this account and server permissions.')


async def smoke(path, demo=False):
    args = StdioServerParameters(command=sys.executable,
        args=['-u', __file__, '--credential', str(path)]+(['--demo'] if demo else []))
    async with Client(args, mode='legacy', read_timeout_seconds=60, cache=None) as client:
        tools = (await client.list_tools()).tools
        if demo: assert {tool.name for tool in tools}==DEMO_TOOLS
    print(f'PASS SDK stdio connection and authenticated host discovery ({len(tools)} tools).')


async def serve(credential, demo=False):
    server = build_server(credential,demo=demo)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main():
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument('--credential', required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    try:
        credential = load(args.credential)
        if args.check:
            async def check():
                async with upstream(credential) as client:
                    tools = await catalog(client)
                    print(f'Host MCP connection verified ({len(tools)} tools).')
            asyncio.run(check())
        elif args.smoke:
            asyncio.run(smoke(args.credential,args.demo))
        else:
            asyncio.run(serve(credential,args.demo))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('Host bridge failed. Check launch.py, the selected server and your MCP key.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
