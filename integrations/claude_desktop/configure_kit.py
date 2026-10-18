"""Preview/add the official-kit server without replacing an existing Claude entry."""
import argparse
import json
from pathlib import Path
import os
import sys
import tempfile
import shutil
from uuid import uuid4
from configure import no_duplicates

ROOT=Path(__file__).resolve().parents[2]
def merge(path,preview):
    path=Path(path)
    original=path.read_bytes() if path.exists() else None
    config=json.loads(original.decode('utf-8-sig'),object_pairs_hook=no_duplicates) if original else {}
    if not isinstance(config,dict): raise ValueError('Invalid config')
    servers=config.setdefault('mcpServers',{})
    if not isinstance(servers,dict): raise ValueError('Invalid servers')
    entry={'command':str(ROOT/'.venv-claude/Scripts/python.exe'),'args':['-u',str(ROOT/'integrations/claude_desktop/kit_bridge.py')]}
    old=servers.get('codebound-kit-alpha')
    if old is not None and old!=entry: raise ValueError('Existing kit entry differs; no overwrite')
    servers['codebound-kit-alpha']=entry
    snippet=ROOT/'.local/kit/claude-entry.json'
    snippet.write_text(json.dumps({'mcpServers':{'codebound-kit-alpha':entry}},indent=2))
    if preview: print('Prepared .local/kit/claude-entry.json; existing Claude configuration unchanged.'); return
    if (path.read_bytes() if path.exists() else None)!=original: raise ValueError('Config changed')
    path.parent.mkdir(parents=True,exist_ok=True)
    if original is not None: shutil.copy2(path,path.with_name(path.name+'.backup-'+uuid4().hex))
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        temp=Path(f.name); f.write(json.dumps(config,indent=2).encode())
    try:
        if (path.read_bytes() if path.exists() else None)!=original: raise ValueError('Config changed')
        os.replace(temp,path)
    finally: temp.unlink(missing_ok=True)
    print('Added only codebound-kit-alpha; other servers/preferences and credentials preserved. Reopen Claude yourself.')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--preview',action='store_true');a=p.parse_args()
    try: merge(a.config,a.preview)
    except Exception: print('Kit Claude configuration failed safely; no secret values displayed.');raise SystemExit(1)
