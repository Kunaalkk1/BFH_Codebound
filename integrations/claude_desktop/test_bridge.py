import asyncio
import json
from pathlib import Path
import subprocess
import sys

from mcp import Client
import mcp_types as types
import pytest
import httpx2

import bridge
import configure
import credentials


@pytest.mark.parametrize("endpoint", ["https://127.0.0.1:4000/mcp/a", "http://example.org:4000/mcp/a",
    "http://127.0.0.1:8000/mcp/a", "http://key@127.0.0.1:4000/mcp/a",
    "http://127.0.0.1:4000/api/users", "http://127.0.0.1:4000/mcp/a?tenant=b"])
def test_endpoint_restriction(endpoint):
    with pytest.raises(credentials.CredentialError):
        credentials.validate_endpoint(endpoint)


def test_dpapi_current_user_roundtrip_only_tenant_a(tmp_path, monkeypatch):
    manifest, protected = tmp_path / "manifest.json", tmp_path / "protected.json"
    key = "mcp_" + "a" * 64
    other_key = "mcp_" + "b" * 64
    endpoint = "http://127.0.0.1:4000/mcp/server-a"
    manifest.write_text(json.dumps({"tenants": {credentials.TENANT: {"endpoint": endpoint, "key": key},
        "demo-tenant-2": {"endpoint": "http://127.0.0.1:4000/mcp/server-b", "key": other_key}}}))
    monkeypatch.setattr(credentials, "MANIFEST", manifest)
    monkeypatch.setattr(credentials, "CREDENTIAL_FILE", protected)
    credentials.prepare()
    assert key not in protected.read_text() and other_key not in protected.read_text()
    assert credentials.load().key == key
    assert key not in repr(credentials.load())
    data = json.loads(protected.read_text())
    data["tenant"] = "demo-tenant-2"
    protected.write_text(json.dumps(data))
    with pytest.raises(credentials.CredentialError):
        credentials.load()


def test_missing_credential_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "CREDENTIAL_FILE", tmp_path / "missing")
    with pytest.raises(credentials.CredentialError, match="Tenant A credential unavailable"):
        credentials.load()


def test_config_preserves_all_servers_and_settings_with_backup(tmp_path):
    target = tmp_path / "Claude/claude_desktop_config.json"
    target.parent.mkdir()
    original = {"preferences": {"enabled": True}, "mcpServers": {"Existing": {"command": "original", "env": {"TOKEN": "test-secret"}}, "existing": {"command": "other"}}}
    raw = json.dumps(original).encode()
    target.write_bytes(raw)
    backup = configure.merge(target, sys.executable, Path(bridge.__file__))
    result = json.loads(target.read_text())
    assert result["preferences"] == original["preferences"]
    for name in ("Existing", "existing"):
        assert result["mcpServers"][name] == original["mcpServers"][name]
    entry = result["mcpServers"]["codebound-tenant-a"]
    assert set(entry) == {"command", "args"} and Path(entry["command"]).is_absolute()
    assert Path(entry["args"][1]).is_absolute()
    assert backup.read_bytes() == raw


@pytest.mark.parametrize("raw", ['{"mcpServers":[]}', '[]', '{invalid', '{"mcpServers":{},"mcpServers":{}}'])
def test_invalid_config_left_unchanged(tmp_path, raw):
    target = tmp_path / "config.json"
    target.write_text(raw)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        configure.merge(target, sys.executable, Path(bridge.__file__))
    assert target.read_text() == raw and not list(tmp_path.glob("*.backup-*"))


def test_preview_and_new_config(tmp_path):
    target = tmp_path / "new/config.json"
    configure.merge(target, sys.executable, Path(bridge.__file__), preview=True)
    assert not target.exists()
    assert configure.merge(target, sys.executable, Path(bridge.__file__)) is None
    assert set(json.loads(target.read_text())["mcpServers"]) == {"codebound-tenant-a"}


def test_offline_handler_errors_do_not_forward_private_tool_or_leak(monkeypatch):
    from contextlib import asynccontextmanager
    secret = "do-not-emit-this-test-key"
    calls = []
    @asynccontextmanager
    async def broken(credential):
        calls.append(True)
        raise RuntimeError(secret)
        yield
    monkeypatch.setattr(bridge, "upstream", broken)
    async def run():
        async with Client(bridge.build_server(credentials.Credential("unused", secret)), mode="legacy", cache=None) as client:
            result = await client.call_tool("private_tool", {})
            assert result.is_error and calls == []
            result = await client.call_tool("codebound_list_datasets", {})
            assert result.is_error and secret not in result.model_dump_json()
    asyncio.run(run())


def test_prepare_and_check_stdout_empty():
    result = subprocess.run([sys.executable, str(Path(bridge.__file__)), "--check"], capture_output=True)
    assert result.returncode == 0 and result.stdout == b""
    assert b"credential ready" in result.stderr


def test_real_stdio_smoke():
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("smoke_test.py"))], capture_output=True, timeout=90)
    assert result.returncode == 0, "Live bridge smoke failed; run smoke_test.py for sanitized diagnostics"
    assert b"BRIDGE SMOKE PASSED" in result.stdout


def test_real_invalid_upstream_auth_is_sanitized():
    valid = credentials.load()
    async def run():
        async with bridge.upstream(credentials.Credential(valid.endpoint, "invalid-test-key")):
            pytest.fail("Unauthenticated upstream connection accepted")
    # SDK task groups may wrap the actual 401 in an ExceptionGroup.
    with pytest.raises(Exception):
        asyncio.run(run())
    # SDK intentionally maps transport authentication failures to generic MCPError.
    # Check HTTP auth separately without handcrafting an MCP protocol message.
    async def probe():
        async with httpx2.AsyncClient(trust_env=False, timeout=3) as client:
            response = await client.get(valid.endpoint, headers={"X-API-Key": "invalid-test-key"})
            assert response.status_code == 401
    asyncio.run(probe())
