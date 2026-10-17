"""Trusted mediated tool calls; credentials and networking never enter the worker."""
from dataclasses import asdict
import json
import re
import subprocess
import sys
from time import perf_counter, monotonic

from codebound.broker_protocol import exchange
from codebound.connector_broker import tool_value
from codebound.docker_executor import LocalDockerExecutor
from codebound.mcp_client import McpClient
from codebound.sandbox import SandboxSettings
from codebound.kit.config import ROOT, state, config

SETTINGS=SandboxSettings(mode='local_docker',worker_image='codebound-worker:kit',timeout_seconds=15)


def secrets():
    s=state(); c=config()
    values=[]
    for u in s['users'].values(): values.extend([u['key'],u['password']])
    for ws in s['canaries']['connectors'].values(): values.extend(ws.values())
    for k in ('hostEnv','hostFile'):
        if s['canaries'].get(k): values.append(s['canaries'][k])
    values.extend(c.get('service_tokens',{}).values())
    return values


def redact_text(text):
    for secret in secrets():
        if secret: text=text.replace(secret,'***')
    return re.sub(r'cbk_[a-z]+_[a-z]+_[0-9a-f]{12,}','***',text)


def identity(key, *, service=False):
    import hmac
    s=state()
    for actor,user in s['users'].items():
        if hmac.compare_digest(key,user['key']): return actor,user['workspace']
    if service:
        for ws,token in config()['service_tokens'].items():
            if hmac.compare_digest(key,token): return ('alice' if ws=='A' else 'bob'),ws
    raise PermissionError('AUTHENTICATION_REQUIRED')


class Broker:
    def __init__(self, actor, workspace, bindings, metrics, registry, sid):
        self.actor,self.workspace,self.bindings=actor,workspace,set(bindings)
        self.metrics,self.registry,self.sid=metrics,registry,sid
        s=state(); ws=s['workspaces'][workspace]
        self.client=McpClient(s['url']+'/mcp/'+ws['serverId'],s['users'][actor]['key'],timeout=3)
        self.client.initialize()
        visible={t['name'] for t in self.client.tools()}
        self.ids=dict(ws['toolIds'])
        raw=config()['workspaces'][workspace].get('raw_offers')
        if raw: self.ids['codebound_raw_supplier_offers']=raw
        self.allowed={name for name in self.ids if name in visible and self.connector(name) in self.bindings}
        self.deadline=monotonic()+18
        self.calls=0; self.input_bytes=0
    @staticmethod
    def connector(name):
        if name=='codebound_raw_supplier_offers': return 'supplier'
        return next((c for c in ('erp','supplier','carrier','payroll') if name.startswith('mock_'+c+'_')),None)
    def __call__(self,name,args):
        self.calls+=1
        if self.calls>64 or monotonic()>self.deadline: raise RuntimeError('CALL_LIMIT_EXCEEDED')
        if name in self.ids.values(): name=next(n for n,i in self.ids.items() if i==name)
        if not isinstance(name,str) or name not in self.allowed or not isinstance(args,dict):
            self.registry.event(self.workspace,self.actor,self.sid,'refused','TOOL_NOT_AUTHORIZED',{})
            return {'error':'TOOL_NOT_AUTHORIZED'}
        self.metrics['workspace_tool_calls']=self.metrics.get('workspace_tool_calls',0)+1
        self.metrics['gateway_tool_names'].append(name)
        try:
            value=tool_value(self.client.call(name,args))
            size=len(json.dumps(value,separators=(',',':')).encode())
            self.input_bytes+=size
            if self.input_bytes>2_097_152: raise RuntimeError('INPUT_LIMIT_EXCEEDED')
            self.metrics['broker_response_bytes']+=self.client.last_response_bytes
            self.metrics['raw_payload_bytes']+=size
            return {'value':json.loads(redact_text(json.dumps(value)))}
        except RuntimeError: raise
        except Exception:
            self.metrics['upstream_errors']=self.metrics.get('upstream_errors',0)+1
            return {'error':'UPSTREAM_TOOL_FAILED'}


def execute(source, parameters, broker, metrics):
    # No caller can select the worker image, network, mounts or limits.
    job={'settings':asdict(SETTINGS),'payload':{'source':source,'input':{'parameters':parameters},'timeout':SETTINGS.timeout_seconds,'output_limit':SETTINGS.max_result_bytes,'brokered':True}}
    if len(json.dumps(job).encode())>SETTINGS.max_input_bytes: raise RuntimeError('INPUT_LIMIT_EXCEEDED')
    started=perf_counter()
    process=subprocess.Popen([sys.executable,'-m','codebound.docker_job'],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    try:
        metrics['docker_attempts']=metrics.get('docker_attempts',0)+1
        result=exchange(process,job,broker,timeout=25,output_limit=SETTINGS.max_result_bytes)
        metrics['docker_executions']=metrics.get('docker_executions',0)+1
        return json.loads(redact_text(json.dumps(result,allow_nan=False)))
    finally:
        metrics['docker_ms']=round((perf_counter()-started)*1000,3)
        if process.stdin:
            try: process.stdin.close()
            except OSError: pass
        # EOF lets the independent trusted helper clean up its exact container.
        try: process.wait(timeout=20)
        except subprocess.TimeoutExpired: raise RuntimeError('HELPER_CLEANUP_TIMEOUT')

