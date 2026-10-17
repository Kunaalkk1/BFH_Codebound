"""Prepare only new local operator files; never overwrite secrets or credentials."""
import json
import secrets
from codebound.kit.config import ROOT,LOCAL

def main():
    LOCAL.mkdir(parents=True,exist_ok=True)
    env=LOCAL/'compose.env'
    if not env.exists(): env.write_text('JWT_SECRET='+secrets.token_hex(32)+'\nENCRYPTION_KEY='+secrets.token_hex(32)+'\nPOSTGRES_PASSWORD='+secrets.token_hex(24)+'\n')
    team=LOCAL/'team.json'
    if not team.exists(): team.write_text(json.dumps({'logs':{'docker':'codebound-kit-app'},'postgres':{'docker':'codebound-kit-postgres','user':'amcp','db':'anythingmcp'},'callTimeoutSeconds':120},indent=2))
    print('Private kit operator configuration ready; existing files preserved.')
if __name__=='__main__': main()
