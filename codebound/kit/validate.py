"""Operator-only exact golden assertions, in addition to the unmodified official checker."""
import json
from codebound.kit.config import KIT,state,LOCAL
from codebound.mcp_client import McpClient
from codebound.connector_broker import tool_value


def main():
    s=state();c=McpClient(s['url']+'/mcp/'+s['workspaces']['A']['serverId'],s['users']['alice']['key']);c.initialize()
    results=[]
    for filename,tool,key in [('processor_offers','mock_supplier_list_offers','items'),('price_drift_check','price_drift_check','rows'),('catalogue_total','catalogue_total',None),('stuck_orders','stuck_orders','rows')]:
        expected=json.loads((KIT/'expected'/(filename+'.json')).read_text())
        for case in expected['calls']:
            if 'result' not in case:continue
            rpc=c.call(tool,case['args'])
            if case['result'] is None or isinstance(case['result'],str):
                assert rpc.get('isError') and 'price_usd' in json.dumps(rpc)
                results.append({'tool':tool,'parameters':case['args'],'status':'PASS','expected_error':'price_usd'})
                print('PASS expected atomic processor refusal: price_usd')
                continue
            response=tool_value(rpc)
            actual=response[key] if key else response
            assert actual==case['result'],tool+' exact result mismatch'
            results.append({'tool':tool,'parameters':case['args'],'status':'PASS'})
            print('PASS exact golden result: '+tool+' '+json.dumps(case['args']))
    (LOCAL/'exact-results-report.json').write_text(json.dumps(results,indent=2))
    return 0
if __name__=='__main__':raise SystemExit(main())
