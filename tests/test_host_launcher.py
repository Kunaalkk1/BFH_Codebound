"""Repair an unavailable default image without replacing host credentials."""
import json
import os
import shutil
import subprocess
from types import SimpleNamespace
import pytest
import launch

OLD = 'helpcodeai/anythingmcp@sha256:old-unavailable-default'
NEW = 'helpcodeai/anythingmcp@sha256:registry-verified-default'


@pytest.fixture
def settings(tmp_path, monkeypatch):
    local = tmp_path/'private-host'
    local.mkdir()
    env = local/'compose.env'
    (tmp_path/'dependencies.json').write_text(json.dumps({
        'anythingmcp-image': NEW, 'anythingmcp-replaced-images': [OLD]}))
    monkeypatch.setattr(launch, 'ROOT', tmp_path)
    monkeypatch.setattr(launch, 'LOCAL', local)
    monkeypatch.setattr(launch, 'ENV', env)
    return local, env


def test_image_repair_preserves_secrets_other_settings_and_original_backup(settings):
    local, env = settings
    original = ('JWT_SECRET=keep-jwt\r\nENCRYPTION_KEY=keep-encryption\r\nPOSTGRES_PASSWORD=keep-db-password\r\n'
        'ANYTHINGMCP_IMAGE='+OLD+'\r\nALLOW_OPEN_REGISTRATION=true\r\n# operator comment\r\n').encode()
    env.write_bytes(original)
    launch.prepare('unused-docker')
    assert env.read_bytes() == original.replace(OLD.encode(), NEW.encode())
    backups = list(local.glob('compose.env.before-image-repair-*'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    launch.prepare('unused-docker')
    assert len(list(local.glob('compose.env.before-image-repair-*'))) == 1


def test_operator_selected_image_is_not_changed(settings):
    local, env = settings
    original = b'JWT_SECRET=own\nENCRYPTION_KEY=own-key\nPOSTGRES_PASSWORD=own-password\nANYTHINGMCP_IMAGE=operator/own-image:v1\n'
    env.write_bytes(original)
    launch.prepare('unused-docker')
    assert env.read_bytes() == original
    assert not list(local.glob('compose.env.before-image-repair-*'))


def test_new_host_uses_current_default_and_fresh_secrets(settings, monkeypatch):
    local, env = settings
    monkeypatch.setattr(launch.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=''))
    launch.prepare('docker')
    values = dict(line.split('=', 1) for line in env.read_text().splitlines())
    assert values['ANYTHINGMCP_IMAGE'] == NEW
    assert len({values[key] for key in ('JWT_SECRET', 'ENCRYPTION_KEY', 'POSTGRES_PASSWORD')}) == 3


def test_missing_config_with_existing_volume_still_refuses(settings, monkeypatch):
    local, env = settings
    monkeypatch.setattr(launch.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='existing-host-volume\n'))
    with pytest.raises(RuntimeError, match='Restore the original configuration'):
        launch.prepare('docker')
    assert not env.exists()


@pytest.mark.parametrize('option', ['setup', 'dependencies_only', 'benchmark', 'test', 'status', 'stop', None])
def test_demo_entrypoints_run_downloaded_scripts_with_process_policy(monkeypatch, option):
    captured = []
    monkeypatch.setattr(launch, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(launch.shutil, 'which', lambda name: 'powershell.exe')
    monkeypatch.setattr(launch, 'run', lambda command: captured.append(command))
    flags = {name: name == option for name in ('setup', 'dependencies_only', 'benchmark', 'test', 'status', 'stop')}
    launch.demo(SimpleNamespace(**flags))
    assert captured[0][:5] == ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File']
    if option in ('setup', 'dependencies_only'):
        assert captured[0][5:] == [launch.ROOT/'setup.ps1', '-Demo'] + (
            ['-DependenciesOnly'] if option == 'dependencies_only' else [])


@pytest.mark.skipif(os.name != 'nt', reason='Windows execution policy regression')
def test_internet_marked_unsigned_script_runs_without_persistent_policy_change(tmp_path):
    shell = shutil.which('powershell.exe')
    if not shell:
        pytest.skip('Windows PowerShell unavailable')
    policies = subprocess.run([shell, '-NoProfile', '-Command',
        'Get-ExecutionPolicy -List | ConvertTo-Json -Compress'], capture_output=True, text=True, check=True).stdout
    script = tmp_path/'unsigned-download.ps1'
    script.write_text("Write-Output 'CODEBOUND_POLICY_PROBE_OK'\n")
    try:
        (tmp_path/'unsigned-download.ps1:Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\n')
    except OSError:
        pytest.skip('Filesystem does not support Windows download zone streams')
    result = subprocess.run([shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'CODEBOUND_POLICY_PROBE_OK' in result.stdout
    after = subprocess.run([shell, '-NoProfile', '-Command',
        'Get-ExecutionPolicy -List | ConvertTo-Json -Compress'], capture_output=True, text=True, check=True).stdout
    assert after == policies
