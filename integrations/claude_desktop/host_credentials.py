"""Host MCP credential storage; independent of demo state and identities."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Credential:
    endpoint: str
    key: str = field(repr=False)


def endpoint(value, *, allow_demo=False):
    if '://' not in value:
        value = 'http://127.0.0.1:4300/mcp/' + value
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'}
            or parsed.port not in ({4200,4300} if allow_demo else {4300}) or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not re.fullmatch(r'/mcp/[A-Za-z0-9_-]{1,128}', parsed.path)):
        raise ValueError('Use your local host MCP server ID or its http://localhost:4300/mcp/... URL')
    return 'http://127.0.0.1:'+str(parsed.port) + parsed.path


def validate(value, *, allow_demo=False):
    if not isinstance(value, dict) or set(value) != {'endpoint', 'key'}:
        raise ValueError('Invalid host credential')
    if not isinstance(value['key'], str) or not re.fullmatch(r'mcp_[0-9a-f]{64}', value['key']):
        raise ValueError('Use a per-user AnythingMCP key beginning with mcp_')
    return Credential(endpoint(value['endpoint'],allow_demo=allow_demo), value['key'])


def encode(credential):
    raw = json.dumps({'endpoint': credential.endpoint, 'key': credential.key}).encode()
    if os.name == 'nt':
        import win32crypt
        return win32crypt.CryptProtectData(raw, 'Codebound host MCP', None, None, None, 1)
    return raw


def load(path):
    path = Path(path)
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise ValueError('Host credential must be readable only by its owner (chmod 600)')
    raw = path.read_bytes()
    if os.name == 'nt':
        import win32crypt
        raw = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
    return validate(json.loads(raw),allow_demo=True)


def save(path, credential, *, rotate=False):
    path = Path(path)
    if path.exists() and not rotate:
        if load(path) != credential:
            raise ValueError('This profile already has different credentials; use --rotate or choose another --name')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        os.chmod(path.parent, 0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            if os.name != 'nt':
                os.chmod(temporary, 0o600)
            handle.write(encode(credential))
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
