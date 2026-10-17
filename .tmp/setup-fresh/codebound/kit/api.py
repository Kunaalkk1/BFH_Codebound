"""Official-kit FastAPI app reusing the script store, Ollama client and Docker policy."""
import ast
import json
import math
from threading import BoundedSemaphore
from time import perf_counter
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field, ConfigDict

from codebound.kit.config import state, config, BINDINGS, SCHEMAS, PROGRAMS
from codebound.kit.registry import Registry
from codebound.kit.runtime import Broker, execute, identity, redact_text, SETTINGS
from codebound.llm.client import OllamaClient, AIError
from codebound.llm.store import fingerprint
from codebound.docker_executor import LocalDockerExecutor
from codebound.metrics import execution_metrics
from codebound.storage import timestamp

class Create(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=120)
    source_code:str=Field(min_length=1,max_length=32000)
    bindings:list[str]=Field(max_length=4)
class Run(BaseModel):
    model_config=ConfigDict(extra='forbid')
    parameters:dict=Field(default_factory=dict)
class ReplaceSource(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_code:str=Field(min_length=1,max_length=32000)
    expected_version:int=Field(ge=1)
class Task(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task:str=Field(min_length=1,max_length=2000)
    parameters:dict=Field(default_factory=lambda:{'threshold':5})
    fresh:bool=False
class Modify(BaseModel):
    model_config=ConfigDict(extra='forbid')
    instruction:str=Field(min_length=1,max_length=2000)
    expected_version:int=Field(ge=1)


def create_app():
    app=FastAPI(title='Codebound Official Hackathon Kit',version='0.3.0')
    registry=Registry(); slots={ws:BoundedSemaphore(2) for ws in ('A','B')}; ollama=OllamaClient()
    app.state.registry=registry
    def auth(authorization,service=False):
        try:
            if not authorization or not authorization.startswith('Bearer '): raise PermissionError()
            return identity(authorization[7:],service=service)
        except PermissionError: raise HTTPException(401,'Authentication required') from None
    def checked_bindings(ws,bindings):
        allowed=set(state()['workspaces'][ws]['connectorIds'])
        # Connector ids are resolved within this workspace, never globally.
        ids=state()['workspaces'][ws]['connectorIds']
        bindings=[next((n for n,i in ids.items() if i==b),b) for b in bindings]
        if not set(bindings)<=allowed: raise HTTPException(403,'Connector binding not authorized')
        return bindings
    def event_metrics(): return {'ollama_calls':0,'ollama_api_requests':0,'generation_ms':0,'docker_executions':0,'workspace_tool_calls':0,'broker_response_bytes':0,'raw_payload_bytes':0,'gateway_tool_names':[]}
    def run_record(actor,ws,record,meta,parameters,metrics=None):
        metrics=metrics if metrics is not None else event_metrics()
        started=perf_counter(); sid=record['script_id']; code=None; status='failed'
        if not slots[ws].acquire(False): raise HTTPException(429,'Workspace capacity exceeded')
        try:
            broker=Broker(actor,ws,json.loads(meta['bindings_json']),metrics,registry,sid)
            result=execute(record['source_code'],parameters,broker,metrics)
            metrics['result_json_bytes']=len(json.dumps(result,separators=(',',':')).encode())
            status='succeeded'
            return result,metrics
        except Exception as exc:
            code=str(exc) if isinstance(exc,RuntimeError) else 'EXECUTION_FAILED'
            if code not in {'TIMED_OUT','SCRIPT_FAILED','CALL_LIMIT_EXCEEDED','OUTPUT_LIMIT_EXCEEDED','INPUT_LIMIT_EXCEEDED','MEMORY_LIMIT_EXCEEDED','INVALID_JSON_RESULT'}: code='EXECUTION_FAILED'
            raise HTTPException(422,code) from None
        finally:
            slots[ws].release(); metrics['total_ms']=round((perf_counter()-started)*1000,3)
            registry.event(ws,actor,sid,status,code,metrics)
    def lookup(ws,sid):
        try: return registry.get(ws,sid)
        except KeyError: raise HTTPException(404,'Script not found') from None
    def save_source(actor,ws,sid,expected_version,source):
        r,m=lookup(ws,sid)
        if m['owner']!=actor: raise HTTPException(403,'Owner required')
        source=redact_text(source)
        if len(source.encode())>64000: raise HTTPException(422,'Source byte limit exceeded')
        try: ast.parse(source)
        except (SyntaxError,ValueError,RecursionError): raise HTTPException(422,'Invalid Python') from None
        if source==r['source_code']: raise HTTPException(409,'Source unchanged')
        with registry.store.connection() as db:
            row=db.execute('UPDATE scripts SET source_code=?,modified_at=?,version=version+1 WHERE tenant_id=? AND script_id=? AND version=? RETURNING *',(source,timestamp(),ws,sid,expected_version)).fetchone()
            if row is None: raise HTTPException(409,'Version conflict')
            db.execute('UPDATE kit_scripts SET identity=NULL WHERE script_id=?',(sid,))
        return dict(row)
    @app.get('/health')
    def health():
        try: LocalDockerExecutor(None,SETTINGS).ready()
        except Exception: raise HTTPException(503,'Kit sandbox unavailable') from None
        return {'status':'ok','sandbox_mode':'local_docker','environment':'official-kit','worker_image':SETTINGS.worker_image}
    @app.get('/kit/discovery')
    def discovery(authorization:str|None=Header(None)):
        actor,ws=auth(authorization)
        s=state()
        return {'actor':actor,'workspace':ws,'organization_id':s['workspaces'][ws]['organizationId'],'tools':list(SCHEMAS),'bindings':list(s['workspaces'][ws]['connectorIds'])}
    @app.post('/kit/scripts')
    def create(body:Create,authorization:str|None=Header(None)):
        actor,ws=auth(authorization)
        try: bindings=checked_bindings(ws,body.bindings)
        except HTTPException:
            registry.event(ws,actor,None,'refused','BINDING_NOT_AUTHORIZED',{})
            raise
        source=redact_text(body.source_code)
        if len(source.encode())>64000: raise HTTPException(422,'Source byte limit exceeded')
        try: ast.parse(source)
        except (SyntaxError,ValueError): raise HTTPException(422,'Invalid Python') from None
        r=registry.create(ws,actor,redact_text(body.name),source,bindings)
        return {'script_id':r['script_id'],'version':r['version']}
    @app.get('/kit/scripts')
    def list_scripts(authorization:str|None=Header(None)):
        actor,ws=auth(authorization)
        return {'scripts':[{'script_id':r['script_id'],'name':r['name'],'version':r['version']} for r in registry.store.list(ws,100,0)]}
    @app.put('/kit/scripts/{sid}')
    def replace(sid:str,body:ReplaceSource,authorization:str|None=Header(None)):
        actor,ws=auth(authorization)
        r=save_source(actor,ws,sid,body.expected_version,body.source_code)
        registry.event(ws,actor,sid,'succeeded','SOURCE_UPDATED',{'version':r['version']})
        return {'script_id':sid,'version':r['version']}
    @app.get('/kit/scripts/{sid}')
    def get(sid:str,authorization:str|None=Header(None)):
        actor,ws=auth(authorization); r,m=lookup(ws,sid)
        return {'script_id':sid,'version':r['version'],'source_code':r['source_code'],'bindings':json.loads(m['bindings_json'])}
    @app.post('/kit/scripts/{sid}/execute')
    def run(sid:str,body:Run,authorization:str|None=Header(None)):
        actor,ws=auth(authorization); r,m=lookup(ws,sid)
        result,metrics=run_record(actor,ws,r,m,body.parameters)
        return {'script_id':sid,'version':r['version'],'result':result,'metrics':metrics}
    @app.delete('/kit/scripts/{sid}')
    def delete(sid:str,authorization:str|None=Header(None)):
        actor,ws=auth(authorization); r,m=lookup(ws,sid)
        if m['owner']!=actor: raise HTTPException(403,'Owner required')
        if any(sid in entry['scripts'].values() for entry in config()['workspaces'].values()):
            raise HTTPException(409,'Published scripts require explicit operator migration')
        with registry.store.connection() as db:
            db.execute('DELETE FROM kit_scripts WHERE script_id=?',(sid,))
            db.execute('DELETE FROM scripts WHERE tenant_id=? AND script_id=?',(ws,sid))
        return {'deleted':True}
    @app.post('/kit/tools/{ws}/{name}')
    def published(ws:str,name:str,parameters:dict,authorization:str|None=Header(None)):
        actor,actual=auth(authorization,service=True)
        if actual!=ws or name not in SCHEMAS: raise HTTPException(404,'Tool not found')
        props=SCHEMAS[name]
        if set(parameters)!=set(props): raise HTTPException(422,'Invalid tool parameters')
        for key,schema in props.items():
            value=parameters[key]
            if schema['type']=='number' and (type(value) not in (int,float) or not math.isfinite(value)): raise HTTPException(422,'Invalid numeric parameter')
            if schema['type']=='string' and (not isinstance(value,str) or len(value)>200): raise HTTPException(422,'Invalid string parameter')
        sid=config()['workspaces'][ws]['scripts'][name]
        r,m=lookup(ws,sid)
        result,metrics=run_record(actor,ws,r,m,parameters)
        return result
    @app.get('/kit/process/{ws}')
    def process(ws:str,category:str,x_api_key:str|None=Header(None)):
        import hmac
        s=state()
        if ws not in ('A','B') or not x_api_key or not hmac.compare_digest(x_api_key,s['canaries']['connectors'][ws]['supplier']): raise HTTPException(401,'Authentication required')
        actor='alice' if ws=='A' else 'bob'
        metrics=event_metrics(); sid=config()['workspaces'][ws]['scripts']['offer_chain']; r,m=lookup(ws,sid)
        broker=Broker(actor,ws,['supplier'],metrics,registry,sid)
        fetched=broker('codebound_raw_supplier_offers',{'category':category})
        if 'error' in fetched: raise HTTPException(502,'Supplier unavailable')
        result,metrics=run_record(actor,ws,r,m,{'response':fetched['value']},metrics)
        if 'error' in result:
            registry.event(ws,actor,sid,'refused','PROCESSOR_INPUT_INVALID',{'path':result['error']['path']})
            raise HTTPException(422,'Invalid processor input at '+result['error']['path'])
        return result
    def generate(actor,ws,task,parameters,metrics):
        task=redact_text(task)
        # This endpoint implements one typed workflow. The task text identifies
        # reuse; arbitrary verification labels must not change its semantics.
        prompt_task="Calculate price drift using parameters[\"threshold\"]." if parameters else task
        reference=(PROGRAMS/'price_drift_check.py').read_text(encoding='utf-8-sig')
        prompt='Return a JSON object with source_code containing a COMPLETE valid Python program. Use this complete implementation as the starting point. It already implements the tool API and correct pagination and batches. Keep all batching and print one JSON object with rows. Do not abbreviate, use ellipses or markdown. The variable parameters already exists. Task: '+prompt_task+'\nComplete Python implementation:\n'+reference
        source=ollama.generate(prompt,metrics)
        if len(source.encode())>64000: raise HTTPException(422,'Generated source byte limit exceeded')
        try: ast.parse(source)
        except SyntaxError:
            registry.event(ws,actor,None,'failed','GENERATED_PYTHON_INVALID',metrics)
            raise HTTPException(422,'Generated Python invalid') from None
        return redact_text(source)
    @app.post('/kit/tasks/run')
    def task(body:Task,authorization:str|None=Header(None)):
        actor,ws=auth(authorization)
        if set(body.parameters)!={'threshold'} or type(body.parameters['threshold']) not in (int,float) or not math.isfinite(body.parameters['threshold']): raise HTTPException(422,'Only typed price_drift_check generation is supported')
        metrics=event_metrics(); token=execution_metrics.set(metrics)
        try:
            signature=fingerprint({'actor':actor,'workspace':ws,'task':body.task,'parameters':body.parameters,'bindings':['erp','supplier']})
            if body.fresh: signature=fingerprint([signature,str(uuid4())])
            found=registry.reusable(ws,signature)
            reused=found is not None
            if found: r,m=found
            else:
                source=generate(actor,ws,body.task,body.parameters,metrics)
                r=registry.create(ws,actor,'Ollama price drift',source,['erp','supplier'],signature,ollama.settings.model)
                r,m=lookup(ws,r['script_id'])
            result,metrics=run_record(actor,ws,r,m,body.parameters,metrics)
            return {'script_id':r['script_id'],'version':r['version'],'script_reused':reused,'ollama_called':bool(metrics['ollama_calls']),'result':result,'metrics':metrics}
        except AIError as exc:
            registry.event(ws,actor,None,'failed',exc.code,metrics)
            raise HTTPException(exc.status,exc.code) from None
        finally: execution_metrics.reset(token)
    @app.post('/kit/scripts/{sid}/modify')
    def modify(sid:str,body:Modify,authorization:str|None=Header(None)):
        actor,ws=auth(authorization); r,m=lookup(ws,sid)
        if m['owner']!=actor: raise HTTPException(403,'Owner required')
        if r['version']!=body.expected_version: raise HTTPException(409,'Version conflict')
        if json.loads(m['bindings_json'])!=['erp','supplier']: raise HTTPException(422,'Only price drift generation is supported')
        metrics=event_metrics(); token=execution_metrics.set(metrics)
        try: source=generate(actor,ws,body.instruction+'\nExisting Python:\n'+r['source_code'],{},metrics)
        finally: execution_metrics.reset(token)
        updated=save_source(actor,ws,sid,body.expected_version,source)
        registry.event(ws,actor,sid,'succeeded','MODIFIED',metrics)
        return {'script_id':sid,'version':updated['version'],'metrics':metrics}
    @app.post('/kit/benchmark/{ws}')
    def benchmark_task(ws:str,body:dict,authorization:str|None=Header(None)):
        actor,actual=auth(authorization,service=True)
        if actual!=ws or set(body)!={'task','threshold','fresh'}: raise HTTPException(422,'Invalid task request')
        request=Task(task=body['task'],parameters={'threshold':body['threshold']},fresh=body['fresh'])
        return task(request,'Bearer '+state()['users'][actor]['key'])
    return app
