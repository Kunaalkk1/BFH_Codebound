"""Run attack cards against this implementation, not against expected responses."""
import json
from pathlib import Path
import sqlite3
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from urllib.parse import urlsplit
from uuid import uuid4
from time import perf_counter
from codebound.kit.config import ROOT, LOCAL, state
from codebound.kit.runtime import secrets

OPENER=build_opener(ProxyHandler({}))
def api(actor,path,body=None,method=None):
    s=state(); key=s['users'][actor]['key']
    req=Request('http://127.0.0.1:8001'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method=method or ('POST' if body is not None else 'GET'))
    try:
        with OPENER.open(req,timeout=50) as r: return r.status,json.load(r)
    except HTTPError as e: return e.code,json.load(e)

def log(port=14180):
    with OPENER.open(f'http://127.0.0.1:{port}/__log',timeout=3) as r: return json.load(r)['entries']

def run_all():
    s=state(); created=[]; results=[]; evidence=[]; current=[None]
    def script(actor,body,bindings=('erp',)):
        nonce='attack-'+uuid4().hex
        source="import json\ntools.call('mock_erp_ping', {'nonce':"+repr(nonce)+"})\n"+body
        status,record=api(actor,'/kit/scripts',{'name':'Disposable attack fixture','source_code':source,'bindings':list(bindings)})
        if status!=200: raise RuntimeError('Fixture creation failed')
        sid=record['script_id']; created.append((actor,sid))
        evidence.append({'card':current[0],'actor':actor,'script_id':sid,'nonce':nonce})
        status,response=api(actor,'/kit/scripts/'+sid+'/execute',{'parameters':{}})
        hits=[e for e in log() if e.get('query',{}).get('nonce')==nonce]
        expected=s['users'][actor]['workspace']
        if not hits or any(e.get('tenant')!=expected for e in hits): raise RuntimeError('Card ping absent or in wrong workspace')
        evidence[-1].update(workspace=expected,mock_sequences=[e['seq'] for e in hits])
        return sid,status,response
    def card(name,fn):
        current[0]=name
        started=perf_counter()
        try:
            details=fn(); results.append({'id':name,'status':'PASS','details':details,'duration_ms':round((perf_counter()-started)*1000,3)})
        except Exception as exc:
            results.append({'id':name,'status':'FAIL','error_type':type(exc).__name__})
        print(name+': '+results[-1]['status'])
    def denied(actor,name,bindings=('erp',)):
        before=max((e['seq'] for e in log()),default=0)
        body="try:\n    value=tools.call("+repr(name)+", {})\n    print(json.dumps({'denied':False}))\nexcept RuntimeError:\n    print(json.dumps({'denied':True}))"
        sid,status,r=script(actor,body,bindings)
        assert status==200 and r['result']['denied'] is True
        assert not any(e['seq']>before and e['service']=='payroll' for e in log())
        with sqlite3.connect(ROOT/'data/codebound.sqlite3') as db:
            assert db.execute("SELECT count(*) FROM kit_events WHERE script_id=? AND status='refused' AND code='TOOL_NOT_AUTHORIZED'",(sid,)).fetchone()[0]>0
        return 'Refused and audited; no Payroll request reached the mock.'
    def containment(body,field):
        _,status,r=script('bob',body)
        assert status==200 and r['result'][field] is True
        return 'Real worker run and nonce verified.'
    def network():
        before=log(14181)
        with OPENER.open('http://127.0.0.1:14181/__whoami',timeout=3) as response:
            outside=json.load(response)['addresses'][0]
        body="import socket\nblocked=[]\nfor host,port in "+repr([(outside,4199),('codebound-carrier',4103)])+":\n    try:\n        socket.create_connection((host,port),timeout=0.25)\n        blocked.append(False)\n    except OSError:\n        blocked.append(True)\nprint(json.dumps({'blocked':all(blocked)}))"
        detail=containment(body,'blocked')
        assert log(14181)==before
        return detail+' Outside-host log unchanged; unbound Carrier connection blocked.'
    def file_separation():
        _,status,r=script('alice',"open('/tmp/workspace-a-secret','w').write('A private scratch')\nprint(json.dumps({'written':True}))")
        assert status==200
        return containment("from pathlib import Path\nprint(json.dumps({'absent':not Path('/tmp/workspace-a-secret').exists()}))",'absent')
    def stopped(body):
        _,status,r=script('bob',body)
        assert status>=400
        _,status,healthy=script('bob',"print(json.dumps({'alive':True}))")
        assert status==200 and healthy['result']['alive'] is True
        return 'Stopped with '+str(r.get('detail'))+'; next sandbox call succeeded.'
    def host_api():
        script('bob',"print(json.dumps({'pinged':True}))")
        status,_=api('bob','/kit/host/scripts',{'source_code':'pass'})
        assert status==404
        return 'Host execution API absent (404).'
    def secret_output():
        secret=s['canaries']['connectors']['B']['supplier']
        sid,status,r=script('bob',"print(json.dumps({'secret':"+repr(secret)+"}))")
        assert status==200 and r['result']['secret']=='***'
        _,saved=api('bob','/kit/scripts/'+sid)
        assert secret not in json.dumps(saved)
        with sqlite3.connect(ROOT/'data/codebound.sqlite3') as db:
            assert secret not in '\n'.join(row[0] for row in db.execute('SELECT metrics_json FROM kit_events'))
        return 'No env credentials injected; explicit canary literal masked before storage and in output/audit.'
    try:
        current[0]='POSITIVE_CONTROL'
        _,control_status,control=script('alice',"value=tools.call('mock_payroll_list_salaries',{})\nprint(json.dumps({'authorized':isinstance(value.get('employees'),list)}))",('erp','payroll'))
        assert control_status==200 and control['result']['authorized'] is True
        card('A1',lambda:denied('bob','mock_payroll_list_salaries'))
        card('A2',lambda:denied('bob',s['workspaces']['A']['toolIds']['mock_payroll_list_salaries']))
        card('A3',lambda:containment("import os\nprint(json.dumps({'absent':os.environ.get('MOCK_SUPPLIER_API_KEY') is None and 'env' not in globals()}))",'absent'))
        card('A4',lambda:containment("import socket\ntry:\n    socket.create_connection(('169.254.169.254',80),timeout=0.25)\n    blocked=False\nexcept OSError:\n    blocked=True\nprint(json.dumps({'blocked':blocked}))",'blocked'))
        card('A5',network)
        card('A6',lambda:containment("import os\nfrom pathlib import Path\nprint(json.dumps({'absent':os.environ.get('CODEBOUND_HOST_CANARY') is None and not Path('/etc/codebound-canary').exists()}))",'absent'))
        card('A7',file_separation)
        card('A8',lambda:stopped('while True: pass'))
        card('A9',lambda:stopped("chunks=[]\nwhile True: chunks.append(bytearray(16*1024*1024))"))
        card('A10',lambda:stopped("for i in range(1000000): tools.call('mock_erp_ping',{'nonce':'quota-loop'})\nprint(json.dumps({'unexpected':True}))"))
        card('A11',lambda:denied('carol','mock_payroll_list_salaries',('erp','payroll')))
        card('A12',host_api)
        card('A13',secret_output)
    finally:
        deleted=0
        for actor,sid in created:
            status,_=api(actor,'/kit/scripts/'+sid,method='DELETE')
            deleted+=status==200
        report={'results':results,'nonce_evidence':evidence,'disposable_scripts_created':len(created),'disposable_scripts_deleted':deleted,'level3':'NOT IMPLEMENTED; no injected env or direct network capability'}
        (LOCAL/'attack-report.json').write_text(json.dumps(report,indent=2))
    return 0 if len(results)==13 and all(r['status']=='PASS' for r in results) else 1
if __name__=='__main__': raise SystemExit(run_all())
