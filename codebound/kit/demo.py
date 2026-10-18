"""Short official-kit demo over the actual workspace MCP server."""
import json
from codebound.kit.config import state
from codebound.mcp_client import McpClient
from codebound.connector_broker import tool_value

def main():
    s=state();c=McpClient(s['url']+'/mcp/'+s['workspaces']['A']['serverId'],s['users']['alice']['key']);c.initialize()
    for name,args in [('price_drift_check',{'threshold':5}),('catalogue_total',{'category':'legacy'}),('stuck_orders',{'days':7,'as_of':'2026-10-16T12:00:00.000Z'})]:
        value=tool_value(c.call(name,args));print(name+': '+json.dumps(value));print('MCP response body bytes:',c.last_response_bytes)
if __name__=='__main__':main()
