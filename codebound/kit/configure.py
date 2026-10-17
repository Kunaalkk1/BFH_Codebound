"""Register project-owned tools in the isolated official-kit workspace servers."""
import json
import secrets
from codebound.kit.config import ROOT, CONFIG, LOCAL, PROGRAMS, BINDINGS, SCHEMAS, state
from codebound.kit.registry import Registry
from codebound.mcp_client import local_request

class Management:
    def __init__(self,actor):
        s=state(); user=s['users'][actor]; self.base=s['url']
        result=self.request('POST','auth/login',{'email':user['email'],'password':user['password']})
        self.token=result['accessToken']
        switched=self.request('POST','organizations/switch',{'organizationId':s['workspaces'][user['workspace']]['organizationId']})
        self.token=switched['accessToken']
    def request(self,method,path,body=None):
        raw,_=local_request(self.base+'/api/'+path,method=method,body=body,headers={'Authorization':'Bearer '+self.token} if hasattr(self,'token') else {})
        return json.loads(raw) if raw else None

def save(c):
    temporary=CONFIG.with_suffix('.tmp'); temporary.write_text(json.dumps(c,indent=2)); temporary.replace(CONFIG)

def configure():
    s=state(); registry=Registry()
    c=json.loads(CONFIG.read_text()) if CONFIG.exists() else {'service_tokens':{},'workspaces':{}}
    for ws,actor in (('A','alice'),('B','bob')):
        api=Management(actor)
        policy=api.request('GET','admin/settings/ssrf-allowed-hosts')
        if 'host.docker.internal' not in policy['hosts']:
            api.request('PUT','admin/settings/ssrf-allowed-hosts',{'hosts':[*policy['hosts'],'host.docker.internal']})
        entry=c['workspaces'].setdefault(ws,{'scripts':{}})
        c['service_tokens'].setdefault(ws,secrets.token_urlsafe(32)); save(c)
        for name,bindings in BINDINGS.items():
            if name not in entry['scripts']:
                r=registry.create(ws,actor,name,(PROGRAMS/(name+'.py')).read_text(encoding='utf-8-sig'),bindings)
                entry['scripts'][name]=r['script_id']; save(c)
        supplier=s['workspaces'][ws]['connectorIds']['supplier']
        all_tools=api.request('GET',f'connectors/{supplier}/tools')
        original=next(t for t in all_tools if t['name']=='mock_supplier_list_offers')
        if 'original_offer_tool' not in entry:
            entry['original_offer_tool']={k:original[k] for k in ('name','description','parameters','endpointMapping')}
            save(c)
        raw=next((t for t in all_tools if t['name']=='codebound_raw_supplier_offers'),None)
        if raw is None:
            definition={**entry['original_offer_tool'],'name':'codebound_raw_supplier_offers','description':'Private processor input; raw supplier offers.'}
            raw=api.request('POST',f'connectors/{supplier}/tools',definition)
        entry['raw_offers']=raw['id']; save(c)
        definition={**entry['original_offer_tool'],'endpointMapping':{'method':'GET','path':f'http://host.docker.internal:8001/kit/process/{ws}','queryParams':{'category':'$category'}}}
        api.request('PUT',f"connectors/{supplier}/tools/{original['id']}",definition)
        if 'connector_id' not in entry:
            conn=api.request('POST','connectors',{'name':'Codebound sandbox scripts','type':'REST','baseUrl':'http://host.docker.internal:8001','authType':'BEARER_TOKEN','authConfig':{'token':c['service_tokens'][ws]}})
            entry['connector_id']=conn['id']; save(c)
        cid=entry['connector_id']
        existing=api.request('GET',f'connectors/{cid}/tools')
        tool_ids=[]
        for name,props in SCHEMAS.items():
            definition={'name':name,'description':'Codebound restricted workspace script: '+name,'parameters':{'type':'object','properties':props,'required':list(props),'additionalProperties':False},'endpointMapping':{'method':'POST','path':f'/kit/tools/{ws}/{name}','bodyMapping':{k:'$'+k for k in props}}}
            tool=next((t for t in existing if t['name']==name),None)
            if tool is None: tool=api.request('POST',f'connectors/{cid}/tools',definition)
            else: api.request('PUT',f"connectors/{cid}/tools/{tool['id']}",definition)
            tool_ids.append(tool['id'])
        definition={'name':'codebound_run_task','description':'Generate or reuse local Python for an authorized ERP/Supplier price drift task.',
                    'parameters':{'type':'object','properties':{'task':{'type':'string'},'threshold':{'type':'number'},'fresh':{'type':'boolean'}},'required':['task','threshold','fresh'],'additionalProperties':False},
                    'endpointMapping':{'method':'POST','path':f'/kit/benchmark/{ws}','bodyMapping':{'task':'$task','threshold':'$threshold','fresh':'$fresh'}}}
        tool=next((t for t in existing if t['name']=='codebound_run_task'),None)
        if tool is None: tool=api.request('POST',f'connectors/{cid}/tools',definition)
        else: api.request('PUT',f"connectors/{cid}/tools/{tool['id']}",definition)
        tool_ids.append(tool['id'])
        # Extend only the isolated workspace's known server assignments.
        server_id=s['workspaces'][ws]['serverId']
        detail=api.request('GET','mcp-servers/'+server_id)
        assigned=[v['connector']['id'] for v in detail['connectors']]
        if cid not in assigned: api.request('PUT',f'mcp-servers/{server_id}/connectors',{'connectorIds':[*assigned,cid]})
        if ws=='A':
            staff=s['workspaces']['A']['roles']['staff']
            staff_ids=[tid for n,tid in s['workspaces']['A']['toolIds'].items() if not n.startswith('mock_payroll_')]
            api.request('PUT',f'roles/{staff}/tools',{'toolIds':[*staff_ids,*tool_ids]})
        print('Registered official-kit workspace '+ws+': processor on original offer tool; three typed sandbox tools.')
    return c

if __name__=='__main__':
    try: configure()
    except Exception as exc:
        print('Kit registration failed ('+type(exc).__name__+'); no remote bodies displayed.')
        raise SystemExit(1)
