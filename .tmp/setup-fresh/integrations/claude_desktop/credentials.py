"""Windows current-user DPAPI snapshot of the existing tenant A service key."""

import base64
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import win32crypt

ROOT = Path(__file__).resolve().parents[2]
CREDENTIAL_FILE = ROOT / ".local/claude-tenant-a.json"
MANIFEST = ROOT / ".local/anythingmcp-integration.json"
TENANT = "demo-tenant-1"


class CredentialError(Exception):
    pass


@dataclass(frozen=True)
class Credential:
    endpoint: str
    key: str = field(repr=False)


def validate_endpoint(endpoint):
    try:
        url = urlsplit(endpoint)
        valid = (url.scheme == "http" and url.hostname == "127.0.0.1" and url.port == 4000
                 and re.fullmatch(r"/mcp/[a-zA-Z0-9_-]+", url.path)
                 and not url.username and not url.password and not url.query and not url.fragment)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise CredentialError("Invalid trusted tenant A endpoint configuration")
    return endpoint


def prepare():
    try:
        entry = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))["tenants"][TENANT]
        # Canonicalize only the existing loopback spelling, not arbitrary hosts.
        endpoint = validate_endpoint(entry["endpoint"].replace("http://localhost:", "http://127.0.0.1:", 1))
        key = entry["key"]
        if not re.fullmatch(r"mcp_[0-9a-f]{64}", key):
            raise ValueError()
        encrypted = win32crypt.CryptProtectData(key.encode(), "Codebound tenant A MCP key", None, None, None, 1)
        data = {"tenant": TENANT, "endpoint": endpoint, "protected_key": base64.b64encode(encrypted).decode()}
        CREDENTIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = CREDENTIAL_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(CREDENTIAL_FILE)
    except Exception:
        raise CredentialError("Cannot prepare tenant A credential; check the existing local integration") from None


def load():
    try:
        data = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8-sig"))
        if data["tenant"] != TENANT:
            raise ValueError()
        endpoint = validate_endpoint(data["endpoint"])
        key = win32crypt.CryptUnprotectData(base64.b64decode(data["protected_key"], validate=True), None, None, None, 1)[1].decode()
        if not re.fullmatch(r"mcp_[0-9a-f]{64}", key):
            raise ValueError()
        return Credential(endpoint, key)
    except Exception:
        raise CredentialError("Tenant A credential unavailable; run launch/configure-claude.ps1 as this Windows user") from None
