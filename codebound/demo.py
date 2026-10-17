"""Explicit local demo factory. Environment tokens are NOT production identity."""

import os
import json
from pathlib import Path

from codebound.auth import Principal
from codebound.main import create_app
from codebound.connector_broker import ConnectorBroker
from codebound.sandbox import SandboxSettings


def create_demo_app():
    token_a = os.environ.get("CODEBOUND_DEMO_TOKEN_A", "")
    token_b = os.environ.get("CODEBOUND_DEMO_TOKEN_B", "")
    if min(len(token_a), len(token_b)) < 32 or token_a == token_b:
        raise RuntimeError("Configure distinct demo tokens of at least 32 characters")
    settings = SandboxSettings.from_env()
    config = Path(__file__).resolve().parent.parent / ".local/anythingmcp-integration.json"
    broker = None
    if settings.mode == "local_docker" and config.exists():
        broker = ConnectorBroker(json.loads(config.read_text()).get("broker", {}), settings.max_input_bytes)
    return create_app(mock_tokens={token_a: Principal("demo-tenant-1"),
                                   token_b: Principal("demo-tenant-2")}, settings=settings, connector_broker=broker)
