"""Official price-drift MCP benchmark. Runtime never reads expected result files."""
import argparse
from datetime import datetime,timezone
import json
import platform
import sqlite3
import subprocess
from importlib.metadata import version
from statistics import median
from time import perf_counter
from uuid import uuid4
from urllib.request import build_opener,ProxyHandler
from codebound.connector_broker import tool_value
from codebound.kit.config import ROOT,KIT,state
from codebound.mcp_client import McpClient
from codebound.llm.client import OllamaClient
from codebound.kit.runtime import secrets
from codebound.docker_executor import docker_executable

HTTP=build_opener(ProxyHandler({}))
TASK='Calculate price_drift_check for all active ERP articles using the current supplier EUR prices.'
def size(value): return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode())
def log():
    with HTTP.open('http://127.0.0.1:14180/__log',timeout=3) as r: return json.load(r)['entries']
def seq(): return max((e['seq'] for e in log()),default=0)
def counts(after):
    entries=[e for e in log() if e['seq']>after]
    if any(e.get('tenant') not in ('A',None) for e in entries): raise ValueError('Concurrent workspace activity; rerun benchmark sequentially')
    return {name:sum(e['service']==name for e in entries) for name in ('erp','supplier','carrier','payroll')}
def verify(value,expected):
    rows=value['rows']
    if len(rows)!=len(expected): raise ValueError('Row count mismatch')
    for a,b in zip(rows,expected):
        if set(a)!=set(b) or any(a[k]!=b[k] if k=='sku' else abs(a[k]-b[k])>.011 for k in b): raise ValueError('Official price drift assertion failed')
def raw_sample(client,expected):
    before=seq(); start=perf_counter(); calls=0; body_bytes=0; payload_bytes=0
    def call(name,args):
        nonlocal calls,body_bytes,payload_bytes
        calls+=1; value=tool_value(client.call(name,args)); body_bytes+=client.last_response_bytes; payload_bytes+=size(value); return value
    articles=[]; page=1
    while True:
        r=call('mock_erp_list_articles',{'status':'active','page':page,'pageSize':100}); articles.extend(r['items'])
        if page>=r['totalPages']: break
        page+=1
    prices={}
    for n in range(0,len(articles),100): prices.update(call('mock_supplier_get_prices',{'skus':[a['sku'] for a in articles[n:n+100]]})['prices'])
    rows=[]
    for a in articles:
        if a['sku'] not in prices or a['purchasePrice']<=0: continue
        drift=(prices[a['sku']]-a['purchasePrice'])/a['purchasePrice']*100
        if drift>5: rows.append({'sku':a['sku'],'purchasePrice':a['purchasePrice'],'supplierPrice':prices[a['sku']],'driftPct':int(drift*10+.5)/10})
    rows.sort(key=lambda a:a['driftPct'],reverse=True)
    result={'rows':rows}; verify(result,expected)
    elapsed=(perf_counter()-start)*1000
    return dict(verified=True,latency_ms=round(elapsed,3),caller_mcp_tool_calls=calls,mcp_response_body_bytes=body_bytes,agent_payload_json_bytes=payload_bytes,raw_dataset_json_bytes=size({'articles':articles,'prices':prices}),compact_result_json_bytes=size(result),result=result,mock_requests=counts(before),codebound_requests=0,ollama_calls=0,ollama_api_requests=0,prompt_eval_count=0,eval_count=0,docker_executions=0,script_reused=None,generation_ms=0)
def run_sample(client,task,expected):
    before=seq()
    with sqlite3.connect(ROOT/'data/codebound.sqlite3') as db:
        after=db.execute('SELECT coalesce(max(rowid),0) FROM kit_events').fetchone()[0]
    start=perf_counter()
    value=tool_value(client.call('codebound_run_task',{'task':task,'threshold':5,'fresh':False}))
    elapsed=(perf_counter()-start)*1000; verify(value['result'],expected)
    metrics=value['metrics']
    with sqlite3.connect(ROOT/'data/codebound.sqlite3') as db:
        events=db.execute("SELECT metrics_json FROM kit_events WHERE rowid>? AND workspace='A' AND actor='alice' AND script_id=? AND status='succeeded' AND code IS NULL",(after,value['script_id'])).fetchall()
    if len(events)!=1 or json.loads(events[0][0])!=metrics: raise ValueError('Ambiguous audit correlation')
    observed=counts(before)
    if sum(observed.values())!=metrics['workspace_tool_calls']: raise ValueError('Broker count and mock request log disagree')
    if value['script_reused'] and (metrics['ollama_calls'] or metrics['ollama_api_requests']): raise ValueError('Reuse generated again')
    return dict(verified=True,latency_ms=round(elapsed,3),caller_mcp_tool_calls=1,mcp_response_body_bytes=client.last_response_bytes,agent_payload_json_bytes=size(value),compact_result_json_bytes=size(value['result']),result=value['result'],mock_requests=observed,codebound_requests=len(events),script_id=value['script_id'],version=value['version'],script_reused=value['script_reused'],**{k:metrics.get(k,0 if k in ('prompt_eval_count','eval_count') and not value['ollama_called'] else None) for k in ('ollama_calls','ollama_api_requests','prompt_eval_count','eval_count','docker_executions','generation_ms','docker_ms','workspace_tool_calls')},audit_metrics=metrics)
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--samples',type=int,choices=range(1,11),default=3); parser.add_argument('--fresh',action='store_true'); opts=parser.parse_args()
    output=ROOT/'benchmarks/results/latest.json'; output.parent.mkdir(parents=True,exist_ok=True)
    report={'status':'failed','timestamp':datetime.now(timezone.utc).isoformat(),'scenario':{'task':'price_drift_check','workspace':'A','actor':'alice','threshold':5},'versions':{'python':platform.python_version()},'methodology':['Sequential authenticated MCP calls through isolated AnythingMCP; no Claude/cloud inference.','MCP bytes are captured HTTP response bodies (JSON/SSE), excluding headers and stdio framing. Agent payload sizes are canonical JSON reserializations. Raw reports sum every ERP/Supplier response; Codebound includes metadata and metrics.','Raw and reused samples report medians; the first execution has one sample. Caller initialization/discovery and optional model warm-up are excluded; per-execution broker initialization and discovery are included in Codebound latency. Mock log capture is outside timed work.','Caller calls are measured at invocation, backend tool calls at broker dispatch, Codebound completed requests via a uniquely correlated run audit. Unobserved retries and internal SQL statements are unavailable.','Ollama counts are reported prompt/evaluation usage. Claude tokens: Not directly measurable.','No saved scripts are deleted. --fresh uses a unique task label, then repeats it exactly for reuse. Smaller agent payload does not mean fewer backend calls or lower latency.']}
    try:
        s=state(); client=McpClient(s['url']+'/mcp/'+s['workspaces']['A']['serverId'],s['users']['alice']['key']); client.initialize()
        expected=json.loads((KIT/'expected/price_drift_check.json').read_text())['calls'][0]['result']
        report['versions']['ollama']=OllamaClient().request('GET','/api/version').get('version')
        report['versions']['codebound']=version('codebound-secure-sandbox')
        report['versions']['fastapi']=version('fastapi')
        report['versions']['sqlite']=sqlite3.sqlite_version
        report['versions']['kit_commit']=subprocess.run(['git','-C',str(KIT),'rev-parse','HEAD'],capture_output=True,text=True,check=True,timeout=10).stdout.strip()
        report['versions']['docker']=subprocess.run([docker_executable(),'version','--format','{{.Server.Version}}'],capture_output=True,text=True,check=True,timeout=10).stdout.strip()
        report['model']=OllamaClient().ready()
        report['raw']=[raw_sample(client,expected) for _ in range(opts.samples)]
        task=TASK+((' Benchmark label '+uuid4().hex) if opts.fresh else '')
        report['task_identity']=task
        report['first']=[run_sample(client,task,expected)]
        report['reused']=[]
        for _ in range(opts.samples):
            sample=run_sample(client,task,expected)
            if not sample['script_reused'] or (sample['script_id'],sample['version'])!=(report['first'][0]['script_id'],report['first'][0]['version']): raise ValueError('Reuse failed')
            report['reused'].append(sample)
        groups=[report['raw'],report['first'],report['reused']]
        print('\nCODEBOUND OFFICIAL KIT BENCHMARK - price_drift_check(threshold=5)')
        print(f"{'Metric (sample medians)':32}{'Raw MCP':>18}{'Codebound first':>20}{'Codebound reused':>20}")
        for title,key in [('MCP response body bytes','mcp_response_body_bytes'),('Agent payload JSON bytes','agent_payload_json_bytes'),('Caller MCP tool calls','caller_mcp_tool_calls'),('Codebound requests','codebound_requests'),('Backend tool calls','workspace_tool_calls'),('Latency ms','latency_ms'),('Ollama generation POSTs','ollama_calls'),('Ollama prompt tokens','prompt_eval_count'),('Ollama output tokens','eval_count'),('Docker executions','docker_executions'),('Generation ms','generation_ms'),('Docker ms','docker_ms')]:
            values=[]
            for group in groups:
                vals=[x.get(key) for x in group]; values.append(str(round(median(vals),3)) if all(type(v) in (int,float) for v in vals) else 'N/A')
            print(f'{title:32}'+''.join(f'{v:>20}' for v in values))
        for name,group in zip(('Raw','First','Reused'),groups): print(name+' mock requests/sample: '+json.dumps(group[0]['mock_requests']))
        reduction=100*(1-median(x['mcp_response_body_bytes'] for x in report['reused'])/median(x['mcp_response_body_bytes'] for x in report['raw']))
        delta=median(x['latency_ms'] for x in report['reused'])-median(x['latency_ms'] for x in report['raw'])
        print(f'MCP body reduction on reuse: {reduction:.2f}%; latency difference: {delta:+.3f} ms.')
        print('First run: '+('existing script reused' if report['first'][0]['script_reused'] else 'fresh Ollama generation'))
        print('Script '+report['first'][0]['script_id']+' version '+str(report['first'][0]['version'])+'; identical script/version reused with zero Ollama calls.')
        print('Claude tokens: Not directly measurable. Backend operations are separate from agent-facing MCP calls.')
        report['status']='passed'
        return 0
    except Exception as exc:
        report['failure_type']=type(exc).__name__; print('BENCHMARK FAILED ('+type(exc).__name__+'); inspect private audit and kit health; no fabricated values.'); return 1
    finally:
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        encoded=json.dumps(report,indent=2,allow_nan=False)
        if any(secret in encoded for secret in secrets()): raise RuntimeError('REPORT_SECRET_DETECTED')
        output.write_text(encoded,encoding='utf-8'); print('Saved benchmarks/results/latest.json')
if __name__=='__main__': raise SystemExit(main())
