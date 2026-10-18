"""Opt-in checks against an EMPTY disposable host gateway on :4300 and real Docker."""
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse, parse_qs
import pytest
from fastapi.testclient import TestClient
from codebound.host.api import create_app
from codebound.host.gateway import Gateway
from codebound.mcp_client import local_request, IntegrationError

pytestmark = pytest.mark.skipif(os.getenv('CODEBOUND_TEST_HOST') != '1',
    reason='Requires an explicitly provisioned empty disposable host validation stack')


@pytest.fixture(scope='module')
def installation(tmp_path_factory):
    raw, _ = local_request('http://127.0.0.1:4300/health/server-info')
    assert json.loads(raw)['hasUsers'] is False, 'Refusing to run against a populated host'
    gateway = Gateway()
    password = 'Host-' + secrets.token_hex(16) + '!'
    email = 'operator-' + secrets.token_hex(4) + '@host-validation.test'
    registration = gateway.request('POST', 'auth/register', body={
        'email': email, 'password': password, 'name': 'Host validation operator', 'acceptTerms': True})
    assert registration['isFirstUser'] is True
    assert registration['user']['role'] == 'ADMIN'
    client = TestClient(create_app(db_path=tmp_path_factory.mktemp('host-live')/'scripts.sqlite3'))
    login = client.post('/session/login', json={'email': email, 'password': password})
    assert login.status_code == 200
    token = login.json()['accessToken']
    header = {'Authorization': 'Bearer '+token}
    servers = client.get('/v1/servers', headers=header).json()['servers']
    assert servers
    server = servers[0]['id']
    # Invite a real separate role through the host's standard user management.
    invite = gateway.request('POST', 'auth/invite', token, {'email':
        'reader-'+secrets.token_hex(4)+'@host-validation.test', 'role': 'VIEWER'})
    invite_token = parse_qs(urlparse(invite['inviteUrl']).query)['token'][0]
    accepted = gateway.request('POST', 'auth/accept-invite', body={
        'token': invite_token, 'password': password, 'name': 'Host validation reader'})
    viewer_header = {'Authorization': 'Bearer '+accepted['accessToken']}
    return client, gateway, token, header, server, viewer_header


def test_first_host_admin_created_by_signup_and_later_signup_invite_only(installation):
    client, gateway, token, header, server, viewer = installation
    assert client.get('/v1/me', headers=header).json()['role'] == 'ADMIN'
    assert client.get('/v1/me', headers=viewer).json()['role'] == 'VIEWER'
    with pytest.raises(IntegrationError, match='HTTP 403'):
        local_request('http://127.0.0.1:4300/api/auth/register', method='POST', body={
            'email': 'uninvited@host-validation.test', 'password': 'StrongPassword123!',
            'name': 'Uninvited', 'acceptTerms': True})


def test_real_host_session_drives_docker_without_demo_state(installation):
    client, gateway, token, header, server, viewer = installation
    body = {'name': 'Independent host script', 'source_code':
        'import json,os\nprint(json.dumps({"answer":parameters["n"]*2,"credential":os.getenv("JWT_SECRET")}))',
        'bindings': []}
    created = client.post('/v1/scripts', headers=header, json=body)
    assert created.status_code == 200
    sid = created.json()['script_id']
    result = client.post('/v1/scripts/'+sid+'/execute', headers=header,
        json={'server_id': server, 'parameters': {'n': 21}})
    assert result.status_code == 200, result.text
    assert result.json()['result'] == {'answer': 42, 'credential': None}
    assert result.json()['metrics']['docker_executions'] == 1
    assert client.get('/v1/scripts/'+sid, headers=viewer).status_code == 404
    assert client.post('/v1/scripts', headers=viewer, json=body).status_code == 403
    assert client.get('/v1/tools/'+server, headers=header).status_code == 200
    assert client.delete('/v1/scripts/'+sid, headers=header).status_code == 200


def test_real_host_calls_a_user_configured_connector_and_refuses_removed_binding(installation):
    client, gateway, token, header, server, viewer = installation
    class Inventory(BaseHTTPRequestHandler):
        def do_GET(self):
            value = b'{"items":[{"quantity":4},{"quantity":7}]}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(value)))
            self.end_headers()
            self.wfile.write(value)
        def log_message(self, *args):
            pass
    service = ThreadingHTTPServer(('0.0.0.0', 0), Inventory)
    Thread(target=service.serve_forever, daemon=True).start()
    try:
        gateway.request('PUT', 'admin/settings/ssrf-allowed-hosts', token, {'hosts': ['host.docker.internal']})
        connector = gateway.request('POST', 'connectors', token, {'name': 'My inventory', 'type': 'REST',
            'baseUrl': 'http://host.docker.internal:'+str(service.server_port), 'authType': 'NONE'})
        tool = gateway.request('POST', 'connectors/'+connector['id']+'/tools', token, {
            'name': 'inventory_current', 'description': 'Current inventory',
            'parameters': {'type': 'object', 'properties': {}},
            'endpointMapping': {'method': 'GET', 'path': '/inventory'}})
        gateway.request('PUT', 'mcp-servers/'+server+'/connectors', token, {'connectorIds': [connector['id']]})
        available = client.get('/v1/tools/'+server, headers=header)
        assert available.status_code == 200
        assert 'inventory_current' in {tool['name'] for tool in available.json()['tools']}
        created = client.post('/v1/scripts', headers=header, json={'name': 'Inventory sum',
            'source_code': 'import json\nr=tools.call("inventory_current",{})\nprint(json.dumps({"quantity":sum(i["quantity"] for i in r["items"])}))',
            'bindings': ['inventory_current']})
        sid = created.json()['script_id']
        result = client.post('/v1/scripts/'+sid+'/execute', headers=header, json={'server_id': server})
        assert result.status_code == 200, result.text
        assert result.json()['result'] == {'quantity': 11}
        assert result.json()['metrics']['workspace_tool_calls'] == 1
        gateway.request('PUT', 'mcp-servers/'+server+'/connectors', token, {'connectorIds': []})
        assert client.post('/v1/scripts/'+sid+'/execute', headers=header,
            json={'server_id': server}).status_code == 403
    finally:
        service.shutdown()
        service.server_close()


def test_real_host_revoked_session_cannot_access_codebound(installation):
    client, gateway, token, header, server, viewer = installation
    profile = gateway.request('GET', 'users/me', token)
    gateway.request('POST', 'users/'+profile['id']+'/revoke-sessions', token, {})
    assert client.get('/v1/me', headers=header).status_code == 401
