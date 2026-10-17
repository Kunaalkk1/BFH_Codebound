"""Official benchmark integrity: aggregation, paging, response bytes and assertions."""
import pytest
from codebound.kit import benchmark


def test_official_assertions_reject_fabricated_or_incomplete_rows():
    expected=[dict(sku='A-test',purchasePrice=100,supplierPrice=110,driftPct=10)]
    benchmark.verify({'rows':expected},expected)
    with pytest.raises(ValueError):benchmark.verify({'rows':[]},expected)
    with pytest.raises(ValueError):benchmark.verify({'rows':[{**expected[0],'driftPct':0}]},expected)


def test_raw_baseline_observes_pagination_batches_and_bytes(monkeypatch):
    articles=[dict(sku='A-'+str(i),purchasePrice=100) for i in range(101)]
    class Client:
        last_response_bytes=1000
        def __init__(self):self.calls=[]
        def call(self,name,args):
            self.calls.append((name,args))
            if name=='mock_erp_list_articles':
                page=args['page']; return {'structuredContent':{'items':articles[(page-1)*100:page*100],'totalPages':2}}
            return {'structuredContent':{'prices':{sku:(120 if sku=='A-0' else 100) for sku in args['skus']}}}
    monkeypatch.setattr(benchmark,'seq',lambda:0)
    monkeypatch.setattr(benchmark,'counts',lambda _:dict(erp=2,supplier=2,carrier=0,payroll=0))
    c=Client(); want=[dict(sku='A-0',purchasePrice=100,supplierPrice=120,driftPct=20)]
    result=benchmark.raw_sample(c,want)
    assert result['caller_mcp_tool_calls']==4
    assert result['mcp_response_body_bytes']==4000
    assert [len(a['skus']) for n,a in c.calls if n=='mock_supplier_get_prices']==[100,1]
    assert result['result']=={'rows':want}


def test_payload_bytes_measure_utf8_json():
    assert benchmark.size({'value':'€'})==len('{"value":"€"}'.encode())
