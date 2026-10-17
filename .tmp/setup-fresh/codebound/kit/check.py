"""Credential-safe workspace discovery checks."""
from codebound.kit.config import state, SCHEMAS
from codebound.mcp_client import McpClient
from codebound.connector_broker import tool_value

def main():
    s=state()
    for actor in ('alice','carol','bob'):
        user=s['users'][actor]; ws=user['workspace']
        c=McpClient(s['url']+'/mcp/'+s['workspaces'][ws]['serverId'],user['key'])
        c.initialize(); names={t['name'] for t in c.tools()}
        assert set(SCHEMAS)<=names
        assert ('mock_payroll_list_salaries' in names)==(actor=='alice')
        ping=tool_value(c.call('mock_erp_ping',{'nonce':'codebound-kit-readiness-'+actor}))
        assert ping['ok'] is True
        print(actor+': workspace '+ws+', typed Codebound tools discovered; payroll access '+('allowed' if actor=='alice' else 'absent'))
    return 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception:
        print('Kit discovery failed; no credentials displayed.'); raise SystemExit(1)
