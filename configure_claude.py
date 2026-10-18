"""Generate Claude Desktop MCP JSON for your own host; --install explicitly merges it."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

from integrations.claude_desktop.host_credentials import endpoint, validate, save, load

ROOT = Path(__file__).resolve().parent
BRIDGE = ROOT / 'integrations/claude_desktop/host_bridge.py'
PYTHON = ROOT / '.venv-claude' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON keys in Claude configuration')
        result[key] = value
    return result


def snippet(name, python, credential, *, demo=False):
    return {'mcpServers': {name: {'command': str(python),
        'args': ['-u', str(BRIDGE), '--credential', str(credential)]+(['--demo'] if demo else [])}}}


def merge(path, entry):
    path = Path(path)
    original = path.read_bytes() if path.exists() else None
    config = json.loads(original.decode('utf-8-sig'), object_pairs_hook=no_duplicates) if original is not None else {}
    if not isinstance(config, dict):
        raise ValueError('Claude configuration must be a JSON object')
    servers = config.setdefault('mcpServers', {})
    if not isinstance(servers, dict):
        raise ValueError('mcpServers must be an object')
    for name, value in entry['mcpServers'].items():
        if name in servers and servers[name] != value:
            raise ValueError('An existing server entry differs. Choose a different --name; existing entry preserved.')
        servers[name] = value
    if (path.read_bytes() if path.exists() else None) != original:
        raise ValueError('Claude configuration changed; retry')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(config, indent=2).encode())
        if (path.read_bytes() if path.exists() else None) != original:
            raise ValueError('Claude configuration changed; retry')
        if original is not None:
            shutil.copy2(path, path.with_name(path.name + '.backup-' + uuid4().hex))
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def claude_config():
    if os.name == 'nt':
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
            '(Get-AppxPackage -Name Claude -ErrorAction SilentlyContinue).PackageFamilyName'],
            capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        families = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(families) > 1:
            raise ValueError('Multiple Claude packages found; specify --config explicitly')
        if families:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', families[0]):
                raise ValueError('Unrecognized Claude package path')
            return Path(os.environ['LOCALAPPDATA']) / 'Packages' / families[0] / 'LocalCache/Roaming/Claude/claude_desktop_config.json'
        return Path(os.environ['APPDATA']) / 'Claude/claude_desktop_config.json'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
    if sys.platform.startswith('linux'):
        base=Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
        if not base.is_absolute(): raise ValueError('XDG_CONFIG_HOME must be an absolute path')
        return base/'Claude/claude_desktop_config.json'
    raise ValueError('Specify --config for your Claude Desktop installation on this platform')


def environment():
    if sys.version_info < (3, 11):
        raise ValueError('Python 3.11+ required')
    if not PYTHON.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(PYTHON.parent.parent)], check=True)
    check = 'from importlib.metadata import version; assert version("mcp")=="2.3.0"; import httpx2, jsonschema'
    if os.name == 'nt':
        check += '; import win32crypt'
    result = subprocess.run([str(PYTHON), '-c', check], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        subprocess.run([str(PYTHON), '-m', 'pip', 'install', '-r',
            str(ROOT / 'integrations/claude_desktop/requirements.txt')], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', help='Your MCP server ID, slug, or local :4300 MCP URL')
    parser.add_argument('--name', help='Claude server entry name')
    parser.add_argument('--demo', action='store_true', help='Use the explicit demo Alice identity and expose only its five reviewed tools')
    parser.add_argument('--install', action='store_true', help='Back up and merge into Claude Desktop configuration')
    parser.add_argument('--config', type=Path, help='Explicit Claude config path (used with --install)')
    parser.add_argument('--rotate', action='store_true', help='Replace this local credential snapshot with a new key')
    args = parser.parse_args()
    args.name=args.name or ('codebound-kit-alpha' if args.demo else 'codebound-host')
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.name):
            raise ValueError('Use letters, digits, underscores or hyphens for --name')
        environment()
        # Re-enter in the isolated SDK environment so Windows DPAPI is available.
        if Path(sys.executable).resolve() != PYTHON.resolve():
            return subprocess.call([str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
        folder = ROOT / '.local/host/claude'
        folder.mkdir(parents=True, exist_ok=True)
        profile = folder / (args.name + ('.dpapi' if os.name == 'nt' else '.json'))
        if args.demo:
            if args.server: raise ValueError('--demo selects the provisioned demo server; omit --server')
            state_file=ROOT/'.local/kit/state/state.json'
            if not state_file.exists(): raise ValueError('Run python3 launch.py demo --setup first')
            state=json.loads(state_file.read_text(encoding='utf-8-sig'))
            credential=validate({'endpoint':state['url']+'/mcp/'+state['workspaces']['A']['serverId'],
                'key':state['users']['alice']['key']},allow_demo=True)
        else:
            existing = load(profile) if profile.exists() and not args.rotate else None
            target = endpoint(args.server or (existing.endpoint if existing else
                input('Your AnythingMCP server ID or MCP URL: ').strip()))
            key = os.environ.get('CODEBOUND_MCP_KEY') or (existing.key if existing else
                getpass.getpass('Your per-user MCP API key (hidden): ').strip())
            credential = validate({'endpoint': target, 'key': key})
        # Verify a temporary snapshot before changing an existing profile.
        probe = folder / ('probe-' + uuid4().hex + profile.suffix)
        clean_env = dict(os.environ)
        clean_env.pop('CODEBOUND_MCP_KEY', None)
        try:
            save(probe, credential)
            subprocess.run([str(PYTHON), str(BRIDGE), '--credential', str(probe), '--smoke']+(['--demo'] if args.demo else []),
                check=True, env=clean_env)
        finally:
            probe.unlink(missing_ok=True)
        save(profile, credential, rotate=args.rotate)
        entry = snippet(args.name, PYTHON, profile,demo=args.demo)
        output = folder / (args.name + '-entry.json')
        output.write_text(json.dumps(entry, indent=2), encoding='utf-8')
        print('Claude JSON generated: ' + str(output))
        print(json.dumps(entry, indent=2))
        if args.install:
            destination = args.config or claude_config()
            merge(destination, entry)
            print('Claude entry installed with existing settings preserved: ' + str(destination))
            print('Save your work and quit/reopen Claude Desktop to load it.')
        else:
            print('Claude Desktop configuration unchanged. Run again with --install to merge automatically.')
        print('Demo profile exposes the five reviewed kit tools.' if args.demo else
            'This exposes tools assigned to your MCP server. Host saved-script APIs remain in the Codebound UI/HTTP API.')
        return 0
    except ValueError as exc:
        print('Claude setup failed: ' + str(exc), file=sys.stderr)
        return 1
    except (OSError, EOFError, subprocess.CalledProcessError):
        print('Claude setup failed. Check dependencies, the local host, server ID, per-user key and configuration path.', file=sys.stderr)
        return 1
    except Exception:
        print('Claude setup failed. Check the saved credential profile and current-user permissions.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
