"""One-time targeted Railway retirement, retaining invocation audits and recoverable backups."""
import json
import re
import sqlite3
import subprocess
from datetime import datetime,timezone
from pathlib import Path
from codebound.docker_executor import docker_executable
from codebound.kit.config import ROOT,LOCAL

def sql(statement):
    r=subprocess.run([docker_executable(),'exec','-i','amcp-postgres','psql','-U','amcp','-d','anythingmcp','-At','-v','ON_ERROR_STOP=1'],input=statement.encode(),capture_output=True,timeout=30)
    if r.returncode: raise RuntimeError('Scoped retirement SQL failed; transaction rolled back')
    return r.stdout.decode().strip()

def main():
    old=ROOT/'.local/anythingmcp-integration.json'; manifest=json.loads(old.read_text())
    if manifest.get('railway_retired'):
        print('Railway registration retirement already recorded; no database changes.');return
    data=manifest['data_server']; ids=list(data['connectors'].values()); sid=data['server_id']; kid=data['key_id']
    if len(ids)!=2 or not all(re.fullmatch('[a-z0-9]+',i) for i in [*ids,sid,kid]): raise ValueError('Unexpected retirement identifiers')
    names=['Codebound PostgreSQL A','Codebound PostgreSQL B']
    backup=ROOT/'.local/migration-backups'/('retirement-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(parents=True)
    with sqlite3.connect(ROOT/'data/codebound.sqlite3') as source,sqlite3.connect(backup/'codebound.sqlite3') as dest:source.backup(dest)
    (backup/'anythingmcp-integration.json').write_bytes(old.read_bytes())
    for db in ('anythingmcp','codebound_railway_demo'):
        with (backup/(db+'.dump')).open('wb') as out:
            subprocess.run([docker_executable(),'exec','amcp-postgres','pg_dump','-U','amcp','-d',db,'-Fc'],stdout=out,stderr=subprocess.PIPE,check=True,timeout=60)
    inlist=','.join("'"+i+"'" for i in ids)
    before_audits=int(sql('SELECT count(*) FROM tool_invocations;'))
    actual=json.loads(sql("SELECT coalesce(json_agg(json_build_object('id',id,'name',name,'type',type,'base_url',base_url)),'[]') FROM connectors WHERE id IN ("+inlist+");"))
    if sorted(c['name'] for c in actual)!=names or any(c['type']!='DATABASE' or c['base_url']!='postgresql://postgres:5432/codebound_railway_demo' for c in actual): raise ValueError('Connector ownership mismatch')
    inventory={'backup':str(backup.relative_to(ROOT)),'connectors':actual,'action':'Disable and detach, retaining connector/tool/key records because hard deletion cascades invocation audits. Retire private server/key. Preserve PostgreSQL service/database/volumes.','private_server_id':sid,'sqlite_rule':'Only ai_scripts whose parsed reference id is postgres-railway-a/b, railway-demo or lufthansa-demo; preserve all audits and unrelated scripts.'}
    (LOCAL/'retirement-inventory.json').write_text(json.dumps(inventory,indent=2))
    statement=f'''BEGIN;
    SELECT 'connectors_disabled='||count(*) FROM connectors WHERE id IN ({inlist}) AND is_active;
    UPDATE connectors SET is_active=false WHERE id IN ({inlist});
    SELECT 'tools_disabled='||count(*) FROM mcp_tools WHERE connector_id IN ({inlist}) AND is_enabled;
    UPDATE mcp_tools SET is_enabled=false WHERE connector_id IN ({inlist});
    SELECT 'assignments_removed='||count(*) FROM mcp_server_connectors WHERE mcp_server_id='{sid}' AND connector_id IN ({inlist});
    DELETE FROM mcp_server_connectors WHERE mcp_server_id='{sid}' AND connector_id IN ({inlist});
    UPDATE mcp_server_configs SET is_active=false WHERE id='{sid}' AND name='Codebound private data broker';
    UPDATE mcp_api_keys SET is_active=false WHERE id='{kid}' AND mcp_server_id='{sid}';
    COMMIT;'''
    inventory['postgres_counts']=sql(statement).splitlines()
    after_audits=int(sql('SELECT count(*) FROM tool_invocations;'))
    if after_audits<before_audits: raise RuntimeError('Audit count decreased')
    inventory['invocation_audits_before']=before_audits;inventory['invocation_audits_after']=after_audits
    with sqlite3.connect(ROOT/'data/codebound.sqlite3') as db:
        old_audits=db.execute('SELECT count(*) FROM execution_audit').fetchone()[0]
        rows=db.execute("SELECT script_id,reference_json FROM ai_scripts WHERE tenant_id IN ('demo-tenant-1','demo-tenant-2')").fetchall()
        targets=[i for i,ref in rows if json.loads(ref).get('id') in {'postgres-railway-a','postgres-railway-b','railway-demo','lufthansa-demo'}]
        deleted=0
        for i in targets:
            deleted+=db.execute("DELETE FROM scripts WHERE script_id=? AND tenant_id IN ('demo-tenant-1','demo-tenant-2')",(i,)).rowcount
            db.execute("DELETE FROM ai_scripts WHERE script_id=? AND tenant_id IN ('demo-tenant-1','demo-tenant-2')",(i,))
        assert db.execute('SELECT count(*) FROM execution_audit').fetchone()[0]==old_audits
    inventory['sqlite_scripts_deleted']=deleted; inventory['obsolete_ai_contexts_deleted']=len(targets);inventory['sqlite_execution_audits_preserved']=old_audits
    manifest['retired_broker']=manifest.pop('broker',{})
    manifest['broker']={};manifest['railway_retired']=True
    temp=old.with_suffix('.tmp');temp.write_text(json.dumps(manifest,indent=2));temp.replace(old)
    inventory['completed']=True
    (LOCAL/'retirement-inventory.json').write_text(json.dumps(inventory,indent=2))
    tool_count=next(line.split('=',1)[1] for line in inventory['postgres_counts'] if line.startswith('tools_disabled='))
    print(f'Retired Codebound PostgreSQL A/B: 2 connectors disabled/detached, {tool_count} tools disabled; private server/key retired.')
    print(f'Removed {deleted} confirmed Railway scripts and {len(targets)} obsolete AI contexts. All invocation/execution audits preserved.')
    print('Recoverable PostgreSQL dumps, SQLite snapshot and configuration backup saved privately; no database or volume dropped.')
if __name__=='__main__':main()
