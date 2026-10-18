"""Claude stdio bridge for the official kit, with a separate DPAPI credential."""
import argparse
import asyncio
import base64
import json
import logging
from pathlib import Path
import sys
import win32crypt

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from codebound.kit.config import state,LOCAL
import bridge

TOOLS=frozenset({'price_drift_check','catalogue_total','stuck_orders','mock_supplier_list_offers','codebound_run_task'})
CREDENTIAL=LOCAL/'claude-alice.dpapi'

def prepare():
    if CREDENTIAL.exists(): raise ValueError('Protected kit credential already exists; explicit rotation required')
    s=state(); value={'endpoint':s['url']+'/mcp/'+s['workspaces']['A']['serverId'],'key':s['users']['alice']['key']}
    protected=win32crypt.CryptProtectData(json.dumps(value).encode(),'Codebound kit Alice',None,None,None,1)
    CREDENTIAL.write_bytes(protected)

def credential():
    import re
    from urllib.parse import urlsplit
    from credentials import Credential
    value=json.loads(win32crypt.CryptUnprotectData(CREDENTIAL.read_bytes(),None,None,None,0)[1])
    url=urlsplit(value['endpoint'])
    if (url.scheme!='http' or url.hostname!='127.0.0.1' or url.port!=4200 or url.query or url.fragment or url.username
            or not re.fullmatch(r'/mcp/[a-z0-9]+',url.path) or not re.fullmatch(r'mcp_[0-9a-f]{64}',value['key'])):
        raise ValueError('Invalid protected kit endpoint/key')
    return Credential(endpoint=value['endpoint'],key=value['key'])

async def smoke():
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    args=StdioServerParameters(command=sys.executable,args=['-u',str(HERE/'kit_bridge.py')],cwd=str(ROOT))
    async with Client(args,mode='legacy',read_timeout_seconds=60,cache=None) as client:
        names={tool.name for tool in (await client.list_tools()).tools}
        assert names==TOOLS
        result=await client.call_tool('price_drift_check',{'threshold':5})
        assert not result.is_error
        value=result.structured_content or json.loads(result.content[0].text)
        assert len(value['rows'])==4 and value['rows'][0]['sku']=='A-10234'
        assert (await client.call_tool('mock_payroll_list_salaries',{})).is_error
    print('PASS official-kit SDK stdio discovery, real sandbox price drift and hidden raw/payroll tools.')

async def serve():
    bridge.TOOLS=TOOLS
    server=bridge.build_server(credential())
    async with bridge.stdio_server() as (read,write):
        await server.run(read,write,server.create_initialization_options())

def main():
    logging.disable(logging.CRITICAL)
    parser=argparse.ArgumentParser(); parser.add_argument('--prepare',action='store_true'); parser.add_argument('--smoke',action='store_true'); parser.add_argument('--check',action='store_true'); opts=parser.parse_args()
    try:
        if opts.prepare: prepare(); print('Separate kit Alice credential protected with current-user DPAPI.')
        elif opts.smoke: asyncio.run(smoke())
        elif opts.check: credential(); print('Official-kit DPAPI bridge credential ready.')
        else: asyncio.run(serve())
        return 0
    except Exception:
        print('Kit bridge failed; check services and the separate protected credential.',file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
