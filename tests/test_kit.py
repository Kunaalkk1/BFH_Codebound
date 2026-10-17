"""Small integrity tests for broker authorization, script logic and immutable fixtures."""
import ast
import json
import subprocess
from pathlib import Path
from queue import Queue
import pytest
from codebound.kit.config import PROGRAMS,ROOT
from codebound.kit.runtime import Broker


def test_registry_tenant_filter(tmp_path,monkeypatch):
    from codebound.kit import registry
    monkeypatch.setattr(registry,'ROOT',tmp_path)
    r=registry.Registry()
    a=r.create('A','alice','Test','pass',['erp'])
    with pytest.raises(KeyError): r.get('B',a['script_id'])
    assert r.get('A',a['script_id'])[1]['owner']=='alice'


def test_broker_workspace_id_and_role_denial_never_dispatches():
    broker=object.__new__(Broker)
    broker.calls=0; broker.deadline=float('inf'); broker.ids={'mock_erp_ping':'B-id'}
    broker.allowed={'mock_erp_ping'}; broker.workspace='B'; broker.actor='bob'; broker.sid='fixture'
    class Audit:
        def __init__(self): self.events=[]
        def event(self,*args): self.events.append(args)
    broker.registry=Audit()
    assert broker('A-payroll-id',{})=={'error':'TOOL_NOT_AUTHORIZED'}
    assert broker('mock_payroll_list_salaries',{})=={'error':'TOOL_NOT_AUTHORIZED'}
    assert len(broker.registry.events)==2
    broker.calls=64
    with pytest.raises(RuntimeError,match='CALL_LIMIT_EXCEEDED'): broker('mock_erp_ping',{})


def test_programs_parse_without_loading_expected_data():
    for p in PROGRAMS.glob('*.py'):
        source=p.read_text(encoding='utf-8-sig')
        ast.parse(source)
        assert 'expected/' not in source and 'A-10234' not in source


def test_kit_fixture_checkout_is_unmodified():
    result=subprocess.run(['git','-C',str(ROOT/'external/codebound-kit'),'status','--porcelain'],capture_output=True,text=True,check=True)
    assert result.stdout==''
