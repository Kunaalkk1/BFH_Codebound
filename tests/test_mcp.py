import copy
import json

import pytest

from codebound.connector_broker import ConnectorBroker
from codebound.datasets import DatasetUnavailableError
from codebound.mcp_client import IntegrationError, decode_rpc, local_request
from codebound.mcp_tools import tools


def test_seven_tools_route_to_existing_authenticated_api():
    definitions = tools()
    assert len(definitions) == 7
    assert len({tool["name"] for tool in definitions}) == 7
    for tool in definitions:
        assert tool["parameters"]["additionalProperties"] is False
        assert "tenant_id" not in tool["parameters"]["properties"]
        assert tool["endpointMapping"]["encodePathParams"] is True
    execution = next(t for t in definitions if t["name"] == "codebound_execute_script")
    assert execution["endpointMapping"]["bodyMapping"] == {"parameters": "$parameters", "reference": "$reference"}
    assert "$defs" in execution["parameters"]


@pytest.mark.parametrize("url", ["https://127.0.0.1:4000/mcp", "http://example.com:4000/mcp",
                                    "http://127.0.0.1:8000/mcp", "http://secret@127.0.0.1:4000/mcp"])
def test_client_rejects_other_destinations(url):
    with pytest.raises(IntegrationError):
        local_request(url)


@pytest.mark.parametrize("raw", [b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}',
                                   b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'])
def test_mcp_json_and_sse(raw):
    assert decode_rpc(raw, 1) == {"ok": True}


@pytest.mark.parametrize("raw", [b'{}', b'not json', b'{"id":1,"error":{"message":"secret"}}'])
def test_protocol_errors_are_sanitized(raw):
    with pytest.raises(IntegrationError) as error:
        decode_rpc(raw, 1)
    assert "secret" not in str(error.value)


def broker(monkeypatch, rows, **extra):
    class FakeClient:
        def __init__(self, endpoint, key):
            pass
        def initialize(self):
            pass
        def call(self, name, arguments):
            assert name == "fixed_tenant_tool" and arguments == {}
            return {"structuredContent": {"rows": rows, "totalRows": len(rows), **extra}}
    monkeypatch.setattr("codebound.connector_broker.McpClient", FakeClient)
    return ConnectorBroker({"a": {"endpoint": "fixed", "key": "secret", "tool": "fixed_tenant_tool", "reference": "own"}}, 1024)


ROW = {"ticket_id": "T1", "tenant_id": "a", "travel_date": "2026-09-01", "ticket_type": "standard", "price_cents": 123}


def test_broker_returns_only_validated_rows(monkeypatch):
    assert broker(monkeypatch, [ROW]).fetch("a", "own") == [ROW]


@pytest.mark.parametrize("tenant,reference", [("b", "own"), ("a", "other")])
def test_connector_authorization_precedes_network(monkeypatch, tenant, reference):
    with pytest.raises(KeyError):
        broker(monkeypatch, []).fetch(tenant, reference)


@pytest.mark.parametrize("change", [{"tenant_id": "b"}, {"price_cents": -1}, {"password": "secret"}, {"price_cents": True}])
def test_broker_rejects_invalid_or_cross_tenant_rows(monkeypatch, change):
    row = {**copy.deepcopy(ROW), **change}
    with pytest.raises(DatasetUnavailableError):
        broker(monkeypatch, [row]).fetch("a", "own")


def test_broker_refuses_truncated_data(monkeypatch):
    with pytest.raises(DatasetUnavailableError):
        broker(monkeypatch, [], truncated=True).fetch("a", "own")


def test_empty_connector_data(monkeypatch):
    assert broker(monkeypatch, []).fetch("a", "own") == []
