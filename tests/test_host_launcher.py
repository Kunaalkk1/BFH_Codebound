"""Repair an unavailable default image without replacing host credentials."""
import json
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
