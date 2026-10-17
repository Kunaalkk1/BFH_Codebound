"""Small loopback-only MCP Streamable HTTP client for the local demo.

No proxy, redirects, URL supplied by tool arguments, or raw remote errors.
Keys stay in this trusted process; never pass a client to a worker.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener


class IntegrationError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def local_request(url, *, method="GET", body=None, headers=None, limit=2_000_000, timeout=45):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.port not in {4000, 4200} or parsed.username or parsed.password or parsed.fragment):
        raise IntegrationError("Only the configured local AnythingMCP API is allowed")
    request = Request(url, method=method, headers={"Content-Type": "application/json", **(headers or {})},
                      data=None if body is None else json.dumps(body).encode())
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise IntegrationError("AnythingMCP response exceeds the byte limit")
            return raw, dict(response.headers)
    except HTTPError as exc:
        raise IntegrationError(f"AnythingMCP HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise IntegrationError("AnythingMCP unavailable") from None


def decode_rpc(raw, request_id):
    try:
        content = raw.decode("utf-8")
        if content.lstrip().startswith("{"):
            messages = [json.loads(content)]
        else:
            messages = [json.loads("\n".join(line[5:].lstrip() for line in event.splitlines()
                                              if line.startswith("data:")))
                        for event in content.replace("\r\n", "\n").split("\n\n")
                        if any(line.startswith("data:") for line in event.splitlines())]
        matches = [item for item in messages if item.get("id") == request_id]
        if len(matches) != 1 or "error" in matches[0] or "result" not in matches[0]:
            raise IntegrationError("MCP request failed")
        return matches[0]["result"]
    except (ValueError, TypeError, AttributeError):
        raise IntegrationError("Invalid MCP response") from None


class McpClient:
    def __init__(self, endpoint, key, timeout=45):
        self.endpoint, self.key = endpoint, key
        self.timeout = timeout
        self.session = None
        self.sequence = 0
        self.last_request_bytes = 0
        self.last_response_bytes = 0

    def rpc(self, method, params):
        from codebound.metrics import execution_metrics
        measurements = execution_metrics.get()
        if measurements is not None:
            measurements["broker_mcp_rpc_attempts"] = measurements.get("broker_mcp_rpc_attempts", 0) + 1
            if method == "tools/call":
                measurements["broker_tool_calls"] = measurements.get("broker_tool_calls", 0) + 1
        self.sequence += 1
        headers = {"X-API-Key": self.key, "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if method != "initialize":
            headers["MCP-Protocol-Version"] = "2025-06-18"
        body = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}
        raw, response_headers = local_request(self.endpoint, method="POST", headers=headers, body=body, timeout=self.timeout)
        self.last_request_bytes = len(json.dumps(body).encode())
        self.last_response_bytes = len(raw)
        for name, value in response_headers.items():
            if name.lower() == "mcp-session-id":
                self.session = value
        return decode_rpc(raw, self.sequence)

    def initialize(self):
        result = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "codebound-verifier", "version": "1"}})
        headers = {"X-API-Key": self.key, "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-06-18"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        local_request(self.endpoint, method="POST", headers=headers,
                      body={"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def tools(self):
        result = self.rpc("tools/list", {})
        tools = result["tools"]
        cursors = set()
        while result.get("nextCursor"):
            if result["nextCursor"] in cursors:
                raise IntegrationError("Repeated MCP catalog cursor")
            cursors.add(result["nextCursor"])
            result = self.rpc("tools/list", {"cursor": result["nextCursor"]})
            tools.extend(result["tools"])
            if len(tools) > 100:
                raise IntegrationError("Unexpected tool catalog size")
        return tools

    def call(self, name, arguments):
        return self.rpc("tools/call", {"name": name, "arguments": arguments})
