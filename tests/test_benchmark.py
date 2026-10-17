"""Benchmark integrity checks independent of the live services."""
import json
import pytest
from codebound.benchmark import calculate, json_bytes, verify, evidence
from codebound.metrics import execution_metrics
from codebound.mcp_client import McpClient


def test_baseline_filters_month_and_uses_row_values():
    rows = [dict(tenant_id='demo-tenant-1',travel_date='2026-09-01',price_cents=12),
            dict(tenant_id='demo-tenant-1',travel_date='2026-10-01',price_cents=999)]
    assert calculate(rows) == dict(ticket_count=1,total_revenue_cents=12)
    with pytest.raises(ValueError): verify(calculate(rows))
    rows[0]['tenant_id']='demo-tenant-2'
    with pytest.raises(ValueError): calculate(rows)


def test_utf8_bytes_include_unicode_and_json_envelope():
    value={'name':'\u20ac'}
    assert json_bytes(value) == len('{"name":"\u20ac"}'.encode('utf-8'))
    assert json_bytes(value) != len('{"name":"\u20ac"}')


def test_mcp_counter_tracks_observed_rpc_attempts(monkeypatch):
    def response(*args,**kwargs):
        body=kwargs['body']
        return json.dumps(dict(jsonrpc='2.0',id=body['id'],result={})).encode(), {}
    monkeypatch.setattr('codebound.mcp_client.local_request', response)
    measured={}
    token=execution_metrics.set(measured)
    try:
        client=McpClient('http://127.0.0.1:4000/mcp/test','not-a-secret')
        client.rpc('initialize',{})
        client.call('schema',{})
        client.call('rows',{})
        assert measured == dict(broker_mcp_rpc_attempts=3,broker_tool_calls=2)
    finally: execution_metrics.reset(token)


def test_audit_correlation_rejects_ambiguous_samples(tmp_path,monkeypatch):
    import sqlite3
    path=tmp_path/'events.sqlite3'
    def connection(): return sqlite3.connect(path)
    monkeypatch.setattr('codebound.benchmark.db_connection',connection)
    with connection() as db:
        db.execute('CREATE TABLE ai_events (tenant_id TEXT, script_id TEXT, operation TEXT, status TEXT, metrics_json TEXT)')
        db.execute("INSERT INTO ai_events VALUES ('demo-tenant-1','script','run','succeeded','{}')")
    assert evidence(0,'script') == {}
    with connection() as db:
        db.execute("INSERT INTO ai_events VALUES ('demo-tenant-1','script','run','succeeded','{}')")
    with pytest.raises(ValueError): evidence(0,'script')
