"""Reconnect only our isolated mock fixtures to our existing official setup state."""
import json
import shutil
from datetime import datetime,timezone
from urllib.request import Request,build_opener,ProxyHandler
from codebound.kit.config import STATE,state,LOCAL

HTTP=build_opener(ProxyHandler({}))
def main():
    s=state()
    # This fixed admin listener belongs to the separately named kit mock project.
    req=Request('http://127.0.0.1:14180/__tenants',method='PUT',data=json.dumps(s['canaries']['connectors']).encode(),headers={'Content-Type':'application/json'})
    with HTTP.open(req,timeout=5) as r:
        if r.status!=200: raise RuntimeError('Mock credential synchronization failed')
    with HTTP.open('http://127.0.0.1:14181/__whoami',timeout=5) as r: addresses=json.load(r)['addresses']
    if not addresses:raise RuntimeError('Outside mock address unavailable')
    backup=LOCAL/('state-before-mock-isolation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
    shutil.copy2(STATE,backup)
    s['mockUrls']['outsideByIp']='http://'+addresses[0]+':4199'
    s['isolatedMockAdmin']='http://127.0.0.1:14180'
    STATE.write_text(json.dumps(s,indent=2))
    print('Our saved fresh official credentials installed only in isolated kit mocks; other installations untouched.')
if __name__=='__main__':main()
