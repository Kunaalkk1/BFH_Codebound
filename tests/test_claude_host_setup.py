"""Generated host config preserves Claude settings and keeps keys out of JSON."""
import importlib.util
import json
import os
from pathlib import Path
import pytest
import configure_claude as setup
from integrations.claude_desktop import host_credentials as credentials


@pytest.mark.parametrize('value', ['my-server', 'http://localhost:4300/mcp/my-server',
    'http://127.0.0.1:4300/mcp/my-server'])
def test_own_host_server_url(value):
    assert credentials.endpoint(value) == 'http://127.0.0.1:4300/mcp/my-server'


@pytest.mark.parametrize('value', ['http://example.com:4300/mcp/a',
    'http://localhost:4200/mcp/demo', 'http://localhost:4300/api/users',
    'http://user:password@localhost:4300/mcp/a', 'http://localhost:4300/mcp/a?key=secret',
    'http://localhost:4300/mcp/a#fragment', '../escape'])
def test_other_destinations_and_embedded_credentials_refused(value):
    with pytest.raises(ValueError):
        credentials.endpoint(value)


def test_generated_json_uses_local_interpreter_and_protected_profile_path(tmp_path):
    key = 'mcp_'+'a'*64
    credential = credentials.validate({'endpoint': 'own-server', 'key': key})
    python, profile = tmp_path/'My Python/python.exe', tmp_path/'private/profile.dpapi'
    entry = setup.snippet('my-own-host', python, profile)
    value = entry['mcpServers']['my-own-host']
    assert value['command'] == str(python)
    assert value['args'][-2:] == ['--credential', str(profile)]
    assert key not in json.dumps(entry) and key not in repr(credential)


def test_install_preserves_existing_servers_preferences_and_backup(tmp_path):
    path = tmp_path/'Claude/config.json'
    path.parent.mkdir()
    original = {'preferences': {'theme': 'dark'}, 'mcpServers': {'other': {'command': 'other-app', 'env': {'KEY': 'old-value'}}}}
    path.write_text(json.dumps(original))
    entry = setup.snippet('my-own-host', Path('/python'), Path('/private/credential'))
    setup.merge(path, entry)
    merged = json.loads(path.read_text())
    assert merged['preferences'] == original['preferences']
    assert merged['mcpServers']['other'] == original['mcpServers']['other']
    assert merged['mcpServers']['my-own-host'] == entry['mcpServers']['my-own-host']
    assert json.loads(next(path.parent.glob('config.json.backup-*')).read_text()) == original


@pytest.mark.parametrize('original', [
    '{"mcpServers":{},"mcpServers":{}}', '{"mcpServers":[]}', '[]',
    '{"mcpServers":{"codebound-host":{"command":"some-other-bridge"}}}'])
def test_invalid_or_conflicting_config_left_unchanged(tmp_path, original):
    path = tmp_path/'config.json'
    path.write_text(original)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        setup.merge(path, setup.snippet('codebound-host', Path('/python'), Path('/credential')))
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name == 'nt' and importlib.util.find_spec('win32crypt') is None,
    reason='DPAPI round-trip requires the separate Claude environment')
def test_snapshot_round_trip_and_explicit_rotation(tmp_path):
    path = tmp_path/'private/profile'
    first = credentials.validate({'endpoint': 'my-server', 'key': 'mcp_'+'a'*64})
    second = credentials.validate({'endpoint': 'my-server', 'key': 'mcp_'+'b'*64})
    credentials.save(path, first)
    assert credentials.load(path) == first
    if os.name == 'nt':
        assert first.key.encode() not in path.read_bytes()
    else:
        assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError):
        credentials.save(path, second)
    assert credentials.load(path) == first
    credentials.save(path, second, rotate=True)
    assert credentials.load(path) == second
