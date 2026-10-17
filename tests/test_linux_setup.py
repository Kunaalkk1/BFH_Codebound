"""Portable demo setup, Docker gateway routing and Linux client profiles."""
import json
from types import SimpleNamespace
from pathlib import Path
import pytest
import launch
import demo_launch
import configure_claude
from integrations.claude_desktop import host_credentials


def test_non_windows_demo_dispatches_without_powershell(monkeypatch):
    calls=[]
    monkeypatch.setattr(launch,'os',SimpleNamespace(name='posix'))
    monkeypatch.setattr(demo_launch,'main',lambda args:calls.append(args))
    args=SimpleNamespace(setup=True)
    launch.demo(args)
    assert calls==[args]


def test_linux_client_config_uses_xdg_directory(tmp_path,monkeypatch):
    monkeypatch.setattr(configure_claude,'os',SimpleNamespace(name='posix',environ={'XDG_CONFIG_HOME':str(tmp_path)}))
    monkeypatch.setattr(configure_claude.sys,'platform','linux')
    assert configure_claude.claude_config()==tmp_path/'Claude/claude_desktop_config.json'


def test_demo_profile_requires_explicit_endpoint_permission():
    value={'endpoint':'http://127.0.0.1:4200/mcp/own-demo','key':'mcp_'+'a'*64}
    with pytest.raises(ValueError): host_credentials.validate(value)
    credential=host_credentials.validate(value,allow_demo=True)
    assert credential.endpoint==value['endpoint']
    assert configure_claude.snippet('kit',Path('/venv/bin/python'),Path('/private/profile'),demo=True)['mcpServers']['kit']['args'][-1]=='--demo'


@pytest.mark.parametrize('address',['0.0.0.0','127.0.0.1','8.8.8.8','::1'])
def test_demo_does_not_bind_all_interfaces_or_public_addresses(monkeypatch,address):
    monkeypatch.setattr(demo_launch.subprocess,'check_output',lambda *args,**kw:address)
    with pytest.raises(RuntimeError): demo_launch.bridge_address('docker')


def test_native_compose_has_linux_route_and_isolated_state(monkeypatch):
    captured=[]
    monkeypatch.setattr(demo_launch,'run',lambda command,**kw:captured.append(command))
    demo_launch.compose('docker',False,'up','-d')
    assert demo_launch.ROOT/'docker/compose.kit-linux.yml' in captured[0]
    assert demo_launch.LOCAL/'compose.env' in captured[0]
    assert demo_launch.ROOT/'docker/compose.host.yml' not in captured[0]


def test_kit_checkout_mismatch_is_preserved(tmp_path,monkeypatch):
    root=tmp_path/'repo';kit=root/'external/codebound-kit';kit.mkdir(parents=True)
    (kit/'custom.txt').write_text('keep operator changes')
    (root/'dependencies.json').write_text(json.dumps({'codebound-kit':{'url':'unused','commit':'tested-commit'}}))
    monkeypatch.setattr(demo_launch,'ROOT',root)
    monkeypatch.setattr(demo_launch,'KIT',kit)
    monkeypatch.setattr(demo_launch.shutil,'which',lambda name:'git')
    monkeypatch.setattr(demo_launch.subprocess,'check_output',lambda *args,**kw:'different-commit')
    with pytest.raises(RuntimeError,match='preserved'): demo_launch.fetch_kit()
    assert (kit/'custom.txt').read_text()=='keep operator changes'
