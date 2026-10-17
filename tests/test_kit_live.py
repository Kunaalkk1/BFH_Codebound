"""Opt-in local Ollama -> real MCP -> mediated workspace tools -> Docker checks."""
import json
import os
from uuid import uuid4
import pytest
from codebound.kit.attacks import api
from codebound.kit.config import KIT,state
from codebound.mcp_client import McpClient
from codebound.connector_broker import tool_value

pytestmark=[pytest.mark.ollama,pytest.mark.skipif(os.getenv('CODEBOUND_TEST_KIT')!='1',reason='Opt in with launch/test.ps1 -Ollama')]

def test_actual_generation_and_exact_reuse():
    s=state();c=McpClient(s['url']+'/mcp/'+s['workspaces']['A']['serverId'],s['users']['alice']['key']);c.initialize()
    args={'task':'Calculate price drift. Verification '+uuid4().hex,'threshold':5,'fresh':False}
    sid=None
    try:
        first=tool_value(c.call('codebound_run_task',args));sid=first['script_id']
        want=json.loads((KIT/'expected/price_drift_check.json').read_text())['calls'][0]['result']
        assert first['result']['rows']==want
        assert first['ollama_called'] and not first['script_reused']
        assert first['metrics']['eval_count']>0 and first['metrics']['docker_executions']==1
        second=tool_value(c.call('codebound_run_task',args))
        assert second['result']==first['result'] and second['script_id']==sid
        assert second['script_reused'] and not second['ollama_called']
        assert second['metrics']['ollama_calls']==second['metrics']['ollama_api_requests']==0
        assert api('bob','/kit/scripts/'+sid)[0]==404
    finally:
        if sid: assert api('alice','/kit/scripts/'+sid,method='DELETE')[0]==200

def test_carol_role_is_enforced_inside_broker():
    status,record=api('carol','/kit/scripts',{'name':'Disposable role verification','source_code':"import json\ntry:\n    tools.call('mock_payroll_list_salaries',{})\n    denied=False\nexcept RuntimeError:\n    denied=True\nprint(json.dumps({'denied':denied}))",'bindings':['payroll']})
    assert status==200
    try:
        status,result=api('carol','/kit/scripts/'+record['script_id']+'/execute',{'parameters':{}})
        assert status==200 and result['result']['denied'] is True
    finally: assert api('carol','/kit/scripts/'+record['script_id'],method='DELETE')[0]==200

def test_versioned_source_updates_enforce_owner_and_version():
    first="import json\nprint(json.dumps({'value':1}))"
    second="import json\nprint(json.dumps({'value':2}))"
    status,record=api('alice','/kit/scripts',{'name':'Disposable version verification','source_code':first,'bindings':[]})
    assert status==200
    sid=record['script_id']
    try:
        body={'source_code':second,'expected_version':1}
        assert api('bob','/kit/scripts/'+sid,body,method='PUT')[0]==404
        assert api('carol','/kit/scripts/'+sid,body,method='PUT')[0]==403
        status,updated=api('alice','/kit/scripts/'+sid,body,method='PUT')
        assert status==200 and updated['version']==2
        assert api('alice','/kit/scripts/'+sid,{'source_code':first,'expected_version':1},method='PUT')[0]==409
        status,result=api('alice','/kit/scripts/'+sid+'/execute',{'parameters':{}})
        assert status==200 and result['version']==2 and result['result']=={'value':2}
    finally: assert api('alice','/kit/scripts/'+sid,method='DELETE')[0]==200
