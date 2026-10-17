"""Sequential, local-only MCP benchmark. Never persists raw rows or credentials."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
from statistics import median
from time import perf_counter
from uuid import uuid4

from codebound.configure_mcp import ROOT, STATE
from codebound.connector_broker import tool_value
from codebound.mcp_client import McpClient, IntegrationError
from codebound.llm.client import OllamaClient
from codebound.llm.store import fingerprint
from codebound.docker_executor import docker_executable

TENANT = 'demo-tenant-1'
TASK = 'Calculate September 2026 ticket count and total revenue in cents. Return ticket_count and total_revenue_cents.'


def json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def calculate(rows):
    selected = [r for r in rows if r['travel_date'][:7] == '2026-09']
    if not all(r['tenant_id'] == TENANT and type(r['price_cents']) is int for r in rows):
        raise ValueError('Baseline tenant/type validation failed')
    return dict(ticket_count=len(selected), total_revenue_cents=sum(r['price_cents'] for r in selected))


def verify(result):
    if result['ticket_count'] != 45 or result['total_revenue_cents'] != 373864:
        raise ValueError('September result assertion failed')


def client(entry):
    result = McpClient(entry['endpoint'], entry['key'])
    result.initialize()
    return result


def db_connection():
    return sqlite3.connect((ROOT / 'data/codebound.sqlite3').as_uri() + '?mode=ro', uri=True)


def watermark():
    with db_connection() as db:
        return db.execute('SELECT coalesce(max(rowid),0) FROM ai_events').fetchone()[0]


def evidence(after, sid):
    with db_connection() as db:
        rows = db.execute("SELECT status,metrics_json FROM ai_events WHERE rowid>? AND tenant_id=? AND script_id=? AND operation='run'", (after, TENANT, sid)).fetchall()
    if len(rows) != 1 or rows[0][0] != 'succeeded':
        raise ValueError('Cannot uniquely correlate successful run audit; stop concurrent benchmark/demo activity')
    return json.loads(rows[0][1])


def raw_sample(mcp, entry):
    start = perf_counter()
    value = tool_value(mcp.call(entry['tool'], {}))
    envelope_bytes = mcp.last_response_bytes
    rows = value['rows']
    if value.get('truncated') or value.get('totalRows') != len(rows):
        raise ValueError('Incomplete baseline data')
    result = calculate(rows)
    verify(result)
    return dict(verified=True, latency_ms=round((perf_counter()-start)*1000,3),
                mcp_tool_calls=1, codebound_completed_requests=0, postgres_data_calls=1,
                postgres_schema_calls=0, ollama_calls=0, ollama_api_requests=0,
                docker_executions=0, raw_dataset_bytes=json_bytes(rows),
                mcp_response_body_bytes=envelope_bytes, tool_payload_bytes=json_bytes(value),
                compact_result_bytes=json_bytes(result), result=result,
                script_reused=None, generation_ms=0, docker_ms=0,
                prompt_eval_count=0, eval_count=0)


def run_sample(mcp, args, baseline):
    after = watermark()
    start = perf_counter()
    value = tool_value(mcp.call('codebound_run_task', args))
    latency = round((perf_counter()-start)*1000,3)
    envelope_bytes = mcp.last_response_bytes
    if value['execution']['status'] != 'succeeded' or value['execution']['execution_mode'] != 'local_docker':
        raise ValueError('Real Docker execution did not succeed')
    result = value['execution']['result']
    verify(result)
    if any(result[k] != baseline[k] for k in baseline):
        raise ValueError('Baseline and Codebound disagree')
    metrics = evidence(after, value['script_id'])
    if bool(metrics['ollama_calls']) != value['ollama_called'] or metrics['script_reused'] != value['script_reused']:
        raise ValueError('Response and audit instrumentation disagree')
    if metrics['ollama_calls'] == 0:
        metrics.update(prompt_eval_count=0, eval_count=0)
    return dict(verified=True, latency_ms=latency, mcp_tool_calls=1,
                codebound_completed_requests=1, mcp_response_body_bytes=envelope_bytes,
                tool_payload_bytes=json_bytes(value), compact_result_bytes=json_bytes(result),
                script_id=value['script_id'], version=value['version'], result=result,
                script_reused=value['script_reused'], raw_dataset_bytes=metrics.get('input_json_canonical_bytes'),
                **{k:metrics.get(k) for k in ('postgres_data_calls','postgres_schema_calls',
                   'broker_tool_calls','broker_mcp_rpc_attempts','ollama_calls','ollama_api_requests',
                   'docker_executions','generation_ms','docker_ms','prompt_eval_count','eval_count')},
                audit_metrics=metrics)


def display(groups):
    labels = ['Raw MCP', 'Codebound first', 'Codebound reused']
    print('\nCODEBOUND BENCHMARK\nScenario: September 2026 railway revenue\n')
    print(f"{'Metric (per sample medians)':34}" + ''.join(f'{s:>20}' for s in labels))
    keys = [('Result verified','verified'),('Caller MCP tool calls','mcp_tool_calls'),
            ('Codebound completed requests','codebound_completed_requests'),
            ('PostgreSQL data tool calls','postgres_data_calls'),('PostgreSQL schema tool calls','postgres_schema_calls'),
            ('Broker MCP tool calls','broker_tool_calls'),('MCP response body bytes','mcp_response_body_bytes'),
            ('Raw dataset JSON bytes','raw_dataset_bytes'),
            ('Tool payload JSON bytes','tool_payload_bytes'),('Compact result JSON bytes','compact_result_bytes'),
            ('Total latency ms','latency_ms'),('Generation ms','generation_ms'),('Docker helper ms','docker_ms'),
            ('Docker executions','docker_executions'),('Ollama generation POSTs','ollama_calls'),
            ('Ollama API requests','ollama_api_requests'),('Ollama prompt tokens','prompt_eval_count'),
            ('Ollama output tokens','eval_count'),('Script reused','script_reused')]
    for title,key in keys:
        values=[]
        for group in groups:
            entries=[s.get(key) for s in group]
            if all(type(x) in (float,int) for x in entries):
                values.append(str(round(median(entries),3)))
            elif all(x is True for x in entries): values.append('true')
            elif all(x is False for x in entries): values.append('false')
            else: values.append('Unavailable')
        print(f'{title:34}' + ''.join(f'{v:>20}' for v in values))
    raw=median(s['mcp_response_body_bytes'] for s in groups[0])
    for label,group in zip(labels[1:],groups[1:]):
        reduction=100*(1-median(s['mcp_response_body_bytes'] for s in group)/raw)
        difference=median(s['latency_ms'] for s in group)-median(s['latency_ms'] for s in groups[0])
        print(f'{label}: MCP body reduction {reduction:.2f}%; latency difference vs raw {difference:+.3f} ms')
    print('First run: ' + ('existing script reused' if groups[1][0]['script_reused'] else 'new script generated'))
    print(f"Script: {groups[1][0]['script_id']} version {groups[1][0]['version']}")
    print('Reuse confirmed: identical script/version, no Ollama generation POSTs.')
    print('Claude tokens: Not directly measurable')


LIMITATIONS = [
    'Direct authenticated HTTP MCP caller substitutes for Claude; Claude Desktop tokens and user-interface latency are not measured.',
    'Raw path uses the existing private authorized PostgreSQL data tool as an agent-boundary counterfactual; the Claude bridge intentionally does not expose raw-data tools.',
    'MCP response bytes are captured HTTP body bytes (JSON/SSE framing included), excluding HTTP headers, TLS and stdio framing. Tool/result sizes are canonical UTF-8 JSON reserializations.',
    'Initialization, discovery, model warm-up and readiness checks are outside timed samples. Raw timing includes independent local aggregation.',
    'Caller calls are counted at invocation; successful Codebound requests correlate to unique run audit events. Gateway retries/other HTTP requests are unavailable, not inferred.',
    'PostgreSQL counts are observed authorized data/schema MCP tool invocations, not SQL statements inside AnythingMCP. Backend broker calls are separate from caller calls.',
    'Ollama tokens come only from reported prompt_eval_count/eval_count; absent counts remain unavailable. Zero generation on reuse is verified separately.',
    'Docker count records successful helper executions; docker_ms includes provisioning, execution and cleanup. Backend operations may exceed the raw path despite smaller agent payloads.',
    'Only one first sample; repeated raw/reuse samples use warm services. No saved scripts are deleted; --fresh creates a unique task label and retains its script.',
]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--samples',type=int,default=3,choices=range(1,11))
    parser.add_argument('--fresh',action='store_true')
    options=parser.parse_args()
    report=dict(timestamp=datetime.now(timezone.utc).isoformat(), status='failed',
                fresh_requested=options.fresh, base_task=TASK,
                scenario=dict(tenant=TENANT, connector='postgres-railway-a', year=2026, month=9),
                methodology=LIMITATIONS, versions=dict(python=platform.python_version(),codebound=version('codebound-secure-sandbox')))
    output=ROOT/'benchmarks/results/latest.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    try:
        config=json.loads(STATE.read_text(encoding='utf-8-sig'))
        broker=config['broker'][TENANT]
        if broker['reference'] != 'postgres-railway-a': raise ValueError('Unexpected broker reference')
        raw=client(broker)
        agent=client(config['tenants'][TENANT])
        report['versions']['ollama']=OllamaClient().request('GET','/api/version').get('version')
        report['model']=OllamaClient().ready()
        report['versions']['docker']=subprocess.run([docker_executable(), 'version', '--format', '{{.Server.Version}}'],capture_output=True,check=True,timeout=10).stdout.decode().strip()
        report['versions']['fastapi']=version('fastapi')
        report['discovery_codebound_tools']=len([t for t in agent.tools() if t['name'].startswith('codebound_')])
        if report['discovery_codebound_tools'] != 10: raise ValueError('Expected ten Codebound tools')
        report['raw']=[raw_sample(raw,broker) for _ in range(options.samples)]
        args=dict(task=TASK + (f' Benchmark label {uuid4().hex}.' if options.fresh else ''),
                  reference=dict(kind='connector',id=broker['reference']),parameters=dict(year=2026,month=9))
        report['task_identity_sha256']=fingerprint(args)
        baseline=report['raw'][0]['result']
        report['first']=[run_sample(agent,args,baseline)]
        report['reused']=[]
        for _ in range(options.samples):
            sample=run_sample(agent,args,baseline)
            first=report['first'][0]
            if (not sample['script_reused'] or sample['ollama_calls'] != 0 or
                    (sample['script_id'],sample['version']) != (first['script_id'],first['version'])):
                raise ValueError('Reuse verification failed')
            report['reused'].append(sample)
        report['status']='passed'
        display([report['raw'],report['first'],report['reused']])
        for limitation in LIMITATIONS: print('- '+limitation)
        return 0
    except Exception as exc:
        # Never serialize upstream exception text, rows, headers or configuration.
        report['failure_type']=type(exc).__name__
        print('BENCHMARK FAILED ('+type(exc).__name__+'); check launch status and local audit/runtime readiness. No fabricated measurements.')
        return 1
    finally:
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        print('Saved benchmarks/results/latest.json (no credentials or raw rows).')


if __name__ == '__main__':
    raise SystemExit(main())
