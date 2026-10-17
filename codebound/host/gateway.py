"""Every operation uses the caller's live AnythingMCP session and tool permissions."""
import json
import re
from dataclasses import dataclass, field
from time import monotonic

from fastapi import HTTPException
from codebound.mcp_client import local_request, McpClient, IntegrationError
from codebound.connector_broker import tool_value

BASE = 'http://127.0.0.1:4300'
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}')


@dataclass(frozen=True)
class Identity:
    actor: str
    workspace: str
    role: str
    token: str = field(repr=False)

    def sanitize(self, value):
        return re.sub(r'mcp_[0-9a-f]{32,}', '***', value.replace(self.token, '***'))


class Gateway:
    def request(self, method, path, token=None, body=None):
        try:
            raw, _ = local_request(BASE + '/api/' + path, method=method, body=body,
                headers={'Authorization': 'Bearer ' + token} if token else {}, timeout=10)
            return json.loads(raw) if raw else None
        except IntegrationError as exc:
            if str(exc) in ('AnythingMCP HTTP 401', 'AnythingMCP HTTP 403'):
                raise HTTPException(401, 'Sign in to AnythingMCP again') from None
            if str(exc) == 'AnythingMCP HTTP 404':
                raise HTTPException(404, 'Resource not found') from None
            raise HTTPException(503, 'AnythingMCP unavailable or request refused') from None

    def authenticate(self, authorization):
        if not authorization or not authorization.startswith('Bearer ') or len(authorization) > 8192:
            raise HTTPException(401, 'AnythingMCP session required')
        token = authorization[7:]
        user = self.request('GET', 'users/me', token)
        if not all(user.get(k) for k in ('id', 'organizationId', 'role')):
            raise HTTPException(401, 'Active workspace required')
        return Identity(user['id'], user['organizationId'], user['role'], token)

    def servers(self, identity):
        # The gateway applies its own current membership and pagination rules.
        return self.request('GET', 'mcp-servers?limit=200', identity.token)

    def client(self, identity, server_id):
        if not IDENTIFIER.fullmatch(server_id):
            raise HTTPException(404, 'MCP server not found')
        server = self.request('GET', 'mcp-servers/' + server_id, identity.token)
        if server.get('organizationId') != identity.workspace or not server.get('isActive', True):
            raise HTTPException(404, 'MCP server not found')
        client = McpClient(BASE + '/mcp/' + server_id, identity.token, timeout=3, bearer=True)
        try:
            client.initialize()
        except IntegrationError:
            raise HTTPException(403, 'MCP server access refused') from None
        return client


class Broker:
    def __init__(self, client, bindings, identity, metrics):
        self.client, self.identity, self.metrics = client, identity, metrics
        visible = {tool['name'] for tool in client.tools()}
        if not set(bindings) <= visible:
            raise HTTPException(403, 'A bound tool is unavailable for this user')
        self.allowed = set(bindings)
        self.calls, self.bytes = 0, 0
        self.deadline = monotonic() + 18

    def __call__(self, name, arguments):
        self.calls += 1
        if self.calls > 64 or monotonic() > self.deadline:
            raise RuntimeError('CALL_LIMIT_EXCEEDED')
        if not isinstance(name, str) or name not in self.allowed or not isinstance(arguments, dict):
            self.metrics['refused_tool_calls'] = self.metrics.get('refused_tool_calls', 0) + 1
            return {'error': 'TOOL_NOT_AUTHORIZED'}
        try:
            # Gateway rechecks this same user's permissions on every call.
            self.metrics['workspace_tool_calls'] += 1
            value = tool_value(self.client.call(name, arguments))
            encoded = json.dumps(value, allow_nan=False)
            self.bytes += len(encoded.encode())
            if self.bytes > 2_097_152:
                raise RuntimeError('INPUT_LIMIT_EXCEEDED')
            return {'value': json.loads(self.identity.sanitize(encoded))}
        except RuntimeError:
            raise
        except IntegrationError:
            return {'error': 'UPSTREAM_TOOL_FAILED'}
