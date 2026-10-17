"""Host authentication, ownership, live permissions and version conflicts."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from codebound.host.api import create_app
from codebound.host.gateway import Identity, Broker, Gateway


class FakeClient:
    def __init__(self, gateway):
        self.gateway = gateway
    def tools(self):
        return [{'name': name, 'inputSchema': {}} for name in self.gateway.allowed]
    def call(self, name, arguments):
        assert name in self.gateway.allowed
        self.gateway.calls.append(name)
        return {'content': [{'type': 'text', 'text': '{"count": 3}'}]}


class FakeGateway:
    def __init__(self):
        self.allowed, self.calls, self.revoked = {'inventory_count'}, [], set()
    def authenticate(self, header):
        token = (header or '').removeprefix('Bearer ')
        if token not in {'owner', 'colleague', 'otherorg', 'viewer'} or token in self.revoked:
            raise HTTPException(401, 'Invalid session')
        return Identity(token, 'org2' if token == 'otherorg' else 'org1',
            'VIEWER' if token == 'viewer' else 'ADMIN', token)
    def servers(self, user):
        return [{'id': 'my-server', 'name': 'My server'}]
    def client(self, user, sid):
        if sid != 'my-server':
            raise HTTPException(404, 'MCP server not found')
        return FakeClient(self)


@pytest.fixture
def host(tmp_path):
    gateway = FakeGateway()
    # Test the complete broker contract without pretending a fake is a real Docker test.
    def runner(source, parameters, broker, metrics, **kwargs):
        result = broker('inventory_count', {})
        if 'error' in result:
            raise RuntimeError('SCRIPT_FAILED')
        return result['value']
    app = create_app(gateway=gateway, db_path=tmp_path/'host.sqlite3', runner=runner, readiness=lambda: None)
    return TestClient(app), gateway, app


def headers(token='owner'):
    return {'Authorization': 'Bearer '+token}


def create(client, token='owner'):
    r = client.post('/v1/scripts', headers=headers(token), json={
        'name': 'Inventory', 'source_code': 'print("ok")', 'bindings': ['inventory_count']})
    assert r.status_code == 200
    return r.json()['script_id']


def test_host_has_no_demo_identity_or_unauthenticated_script_access(host):
    client, gateway, app = host
    assert client.get('/health').json()['environment'] == 'host'
    for token in ('alice', 'bob', 'carol', ''):
        assert client.get('/v1/scripts', headers=headers(token)).status_code == 401
    assert client.post('/v1/scripts', headers=headers('viewer'), json={
        'name': 'Blocked', 'source_code': 'pass'}).status_code == 403


def test_host_scripts_owned_by_real_user_and_workspace(host):
    client, gateway, app = host
    sid = create(client)
    for other in ('colleague', 'otherorg'):
        assert client.get('/v1/scripts/'+sid, headers=headers(other)).status_code == 404
        assert client.get('/v1/scripts', headers=headers(other)).json() == {'scripts': []}
        assert client.delete('/v1/scripts/'+sid, headers=headers(other)).status_code == 404
    assert client.get('/v1/scripts/'+sid, headers=headers()).status_code == 200


def test_host_revalidates_session_and_tools_each_execution(host):
    client, gateway, app = host
    sid = create(client)
    endpoint = '/v1/scripts/'+sid+'/execute'
    body = {'server_id': 'my-server', 'parameters': {}}
    assert client.post(endpoint, headers=headers(), json=body).json()['result'] == {'count': 3}
    gateway.allowed.clear()
    assert client.post(endpoint, headers=headers(), json=body).status_code == 403
    assert gateway.calls == ['inventory_count']
    gateway.revoked.add('owner')
    assert client.post(endpoint, headers=headers(), json=body).status_code == 401
    with app.state.registry.store.connection() as db:
        assert [row['status'] for row in db.execute('SELECT status FROM kit_events')] == ['succeeded', 'failed']


def test_host_updates_compare_version_and_invalidate_stale_edits(host):
    client, gateway, app = host
    sid = create(client)
    body = {'name': 'Updated', 'source_code': 'print("updated")', 'bindings': [], 'expected_version': 1}
    assert client.put('/v1/scripts/'+sid, headers=headers(), json=body).json()['version'] == 2
    assert client.put('/v1/scripts/'+sid, headers=headers(), json=body).status_code == 409
    assert client.get('/v1/scripts/'+sid, headers=headers()).json()['bindings'] == []


def test_broker_denies_unbound_tools_and_enforces_call_cap():
    gateway, metrics = FakeGateway(), {'workspace_tool_calls': 0}
    broker = Broker(FakeClient(gateway), ['inventory_count'], Identity('u', 'org', 'ADMIN', 'token'), metrics)
    assert broker('private_payroll', {}) == {'error': 'TOOL_NOT_AUTHORIZED'}
    assert gateway.calls == []
    broker.calls = 64
    with pytest.raises(RuntimeError, match='CALL_LIMIT_EXCEEDED'):
        broker('inventory_count', {})


def test_gateway_checks_server_organization_before_mcp(monkeypatch):
    gateway = Gateway()
    monkeypatch.setattr(gateway, 'request', lambda *args: {'organizationId': 'foreign', 'isActive': True})
    with pytest.raises(HTTPException) as error:
        gateway.client(Identity('u', 'org', 'ADMIN', 'private-token'), 'foreign-server')
    assert error.value.status_code == 404


def test_saved_source_masks_callers_credential(host):
    client, gateway, app = host
    r = client.post('/v1/scripts', headers=headers(), json={
        'name': 'Secret check', 'source_code': 'print("owner")', 'bindings': []})
    sid = r.json()['script_id']
    assert client.get('/v1/scripts/'+sid, headers=headers()).json()['source_code'] == 'print("***")'
