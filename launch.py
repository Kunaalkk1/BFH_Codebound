"""Cross-platform entry point: independent host by default, fixtures via demo."""
import argparse
import hashlib
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local/host'
ENV, STATE = LOCAL / 'compose.env', LOCAL / 'backend.json'
PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
OPENER = build_opener(ProxyHandler({}))


@contextmanager
def host_lock():
    LOCAL.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        os.chmod(LOCAL, 0o700)
    with (LOCAL / 'operation.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another host operation is running; wait for it to finish.') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def source_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'codebound').rglob('*.py')):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run(args, **kwargs):
    subprocess.run([str(item) for item in args], cwd=ROOT, check=True, **kwargs)


def read(url):
    with OPENER.open(url, timeout=3) as response:
        return json.load(response)


def probe(url):
    try:
        return read(url)
    except (URLError, OSError, ValueError):
        return None


def dependencies():
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11+ is required')
    if not PYTHON.exists():
        if (ROOT / '.venv').exists():
            raise RuntimeError('Existing .venv is incomplete or belongs to another OS. Rename it, then retry to create a local environment.')
        try: run([sys.executable, '-m', 'venv', ROOT / '.venv'])
        except subprocess.CalledProcessError:
            raise RuntimeError('Could not create .venv. On Debian/Ubuntu install python3-venv; then retry with Python 3.11+.') from None
    valid = subprocess.run([str(PYTHON), '-I', '-c',
        'import sys; sys.exit(not (sys.version_info >= (3, 11) and sys.prefix != sys.base_prefix))'],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if valid.returncode:
        raise RuntimeError('Existing .venv requires Python 3.11+ on this OS. Rename .venv and rerun with Python 3.11+ to recreate it.')
    check = subprocess.run([str(PYTHON), '-c', 'import fastapi, uvicorn, codebound.host.api'],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        run([PYTHON, '-m', 'pip', 'install', '-e', '.[dev]'])


def worker(executable):
    digest = hashlib.sha256()
    for name in ('Dockerfile', 'runner.py', 'brokered.py'):
        digest.update((ROOT / 'sandbox-worker' / name).read_bytes())
    expected = digest.hexdigest()
    image = subprocess.run([executable, 'image', 'inspect', 'codebound-worker:host',
        '--format', '{{index .Config.Labels "codebound.source"}}'], capture_output=True, text=True)
    if image.returncode or image.stdout.strip() != expected:
        run([executable, 'build', '--label', 'codebound.source=' + expected,
            '-t', 'codebound-worker:host', ROOT / 'sandbox-worker'])


def docker():
    executable = shutil.which('docker')
    if not executable and os.name == 'nt':
        candidates = [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Docker/Docker/resources/bin/docker.exe',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/DockerDesktop/resources/bin/docker.exe']
        executable = next((str(path) for path in candidates if path.is_file()), None)
    if not executable:
        raise RuntimeError('Install Docker Desktop (Linux engine) or Docker Engine + Compose v2.')
    result = subprocess.run([executable, 'info', '--format', '{{.OSType}}'], capture_output=True, text=True)
    if result.returncode or result.stdout.strip() != 'linux':
        raise RuntimeError('Docker Linux engine is unavailable. Start Docker and verify current-user access with docker info.')
    version=subprocess.run([executable,'compose','version','--short'],capture_output=True,text=True)
    match=re.match(r'v?(\d+)\.(\d+)\.(\d+)',version.stdout.strip())
    if version.returncode or not match or tuple(map(int,match.groups())) < (2,24,4):
        raise RuntimeError('Install/update Docker Compose v2.24.4+ (docker compose plugin).')
    return executable


def repair_image_config(config):
    """Validate and migrate a known retired image without changing secrets."""
    dependencies = json.loads((ROOT / 'dependencies.json').read_text())
    original = config.read_bytes()
    content = original.decode('utf-8-sig')
    entries = dict(line.split('=', 1) for line in content.splitlines() if '=' in line and not line.startswith('#'))
    keys = set(entries)
    if not {'JWT_SECRET', 'ENCRYPTION_KEY', 'POSTGRES_PASSWORD', 'ANYTHINGMCP_IMAGE'} <= keys:
        raise RuntimeError(f'Incomplete configuration: {config}. Restore its backup; secrets will not be replaced.')
    if entries['ANYTHINGMCP_IMAGE'] in dependencies.get('anythingmcp-replaced-images', []):
        replacement = dependencies['anythingmcp-image']
        content = ''.join('ANYTHINGMCP_IMAGE=' + replacement + ('\r\n' if line.endswith('\r\n') else '\n')
            if line.startswith('ANYTHINGMCP_IMAGE=') else line for line in content.splitlines(keepends=True))
        backup = config.with_name('compose.env.before-image-repair-' + secrets.token_hex(6))
        with backup.open('xb') as handle:
            if os.name != 'nt':
                os.chmod(backup, 0o600)
            handle.write(original)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=config.parent, delete=False) as handle:
                temporary = Path(handle.name)
                if os.name != 'nt':
                    os.chmod(temporary, 0o600)
                handle.write(content.encode())
            if config.read_bytes() != original:
                raise RuntimeError('Host configuration changed during image repair; retry.')
            os.replace(temporary, config)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        print('Repaired the unavailable AnythingMCP image reference; secrets preserved and original configuration backed up.')
    return


def prepare(executable):
    LOCAL.mkdir(parents=True, exist_ok=True)
    dependencies = json.loads((ROOT / 'dependencies.json').read_text())
    if ENV.exists():
        repair_image_config(ENV)
        return
    volumes = subprocess.run([executable, 'volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=codebound-host'],
        check=True, capture_output=True, text=True).stdout.strip()
    if volumes:
        raise RuntimeError(f'Existing host volume found without {ENV}. Restore the original configuration.')
    image = dependencies['anythingmcp-image']
    with ENV.open('x', encoding='utf-8') as output:
        if os.name != 'nt':
            os.chmod(ENV, 0o600)
        output.write(''.join(f'{key}={secrets.token_hex(32)}\n'
            for key in ('JWT_SECRET', 'ENCRYPTION_KEY', 'POSTGRES_PASSWORD')))
        output.write(f'ANYTHINGMCP_IMAGE={image}\nALLOW_OPEN_REGISTRATION=false\n')


def compose(executable, *options):
    clean = dict(os.environ)
    for key in ('COMPOSE_PROJECT_NAME', 'JWT_SECRET', 'ENCRYPTION_KEY', 'POSTGRES_PASSWORD',
                'ANYTHINGMCP_IMAGE', 'ALLOW_OPEN_REGISTRATION'):
        clean.pop(key, None)
    run([executable, 'compose', '--project-name', 'codebound-host', '--env-file', ENV,
        '-f', ROOT / 'docker/compose.host.yml', *options], env=clean)


def api_state():
    return json.loads(STATE.read_text()) if STATE.exists() else None


def stop_api():
    state = api_state()
    if not state:
        return
    live = probe('http://127.0.0.1:8002/instance')
    if live and live.get('instance') != state['instance']:
        raise RuntimeError('Port 8002 belongs to another API. It was left untouched.')
    (LOCAL / ('stop-' + state['instance'])).touch()
    for _ in range(80):
        if not probe('http://127.0.0.1:8002/instance'):
            STATE.unlink(missing_ok=True)
            return
        time.sleep(.25)
    raise RuntimeError('Host API stop timed out; inspect .local/host/backend.err.log')


def start_api():
    state, live = api_state(), probe('http://127.0.0.1:8002/instance')
    if live:
        if not state or live.get('instance') != state['instance']:
            raise RuntimeError('Port 8002 is already managed elsewhere; leaving it untouched.')
        if state.get('source_digest') == source_digest():
            read('http://127.0.0.1:8002/health')
            return
        stop_api()
    instance, environment = secrets.token_hex(16), dict(os.environ)
    model = ROOT / '.local/ollama.json'
    if not environment.get('OLLAMA_MODEL') and model.exists():
        environment['OLLAMA_MODEL'] = json.loads(model.read_text(encoding='utf-8-sig'))['model']
    with (LOCAL / 'backend.out.log').open('ab') as out, (LOCAL / 'backend.err.log').open('ab') as err:
        process = subprocess.Popen([str(PYTHON), '-m', 'codebound.host.server', instance],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')
    STATE.write_text(json.dumps({'pid': process.pid, 'instance': instance, 'source_digest': source_digest()}))
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        live = probe('http://127.0.0.1:8002/instance')
        if live and live.get('instance') == instance and probe('http://127.0.0.1:8002/health'):
            return
        if process.poll() is not None:
            raise RuntimeError('Host API exited; inspect .local/host/backend.err.log')
        time.sleep(.5)
    raise RuntimeError('Host API startup timed out; inspect .local/host/backend.err.log')


def host(args):
    if args.test:
        dependencies()
        run([PYTHON, '-m', 'pytest', '-q', '-m', 'not docker'])
        return
    if args.benchmark:
        raise RuntimeError('Use python launch.py demo --benchmark for the fixture benchmark.')
    if args.dependencies_only:
        dependencies()
        print('Host dependencies ready. No kit, users or credentials created.')
        return
    executable = docker()
    if args.stop:
        if ENV.exists():
            stop_api()
            compose(executable, 'stop', '--timeout', '20')
        print('Host stopped. Accounts, scripts and volumes retained.')
        return
    if args.status:
        if not ENV.exists():
            raise RuntimeError('Host is not configured. Run python launch.py')
        compose(executable, 'ps')
        live, state = read('http://127.0.0.1:8002/instance'), api_state()
        if not state or live.get('instance') != state['instance']:
            raise RuntimeError('Host API ownership could not be verified')
        read('http://127.0.0.1:8002/health')
    else:
        dependencies()
        prepare(executable)
        compose(executable, 'up', '-d', '--wait', '--wait-timeout', '240')
        worker(executable)
        start_api()
    info = read('http://127.0.0.1:4300/health/server-info')
    print('HOST READY | AnythingMCP http://localhost:3200 | Codebound http://127.0.0.1:8002')
    print('Sign in with your own account.' if info['hasUsers'] else
        'Create YOUR first administrator at http://localhost:3200/login. No demo accounts exist.')
    print('Admins invite additional users in AnythingMCP. Ollama is optional for generation.')


def demo(args):
    if os.name != 'nt':
        from demo_launch import main as native_demo
        return native_demo(args)
    shell = shutil.which('powershell.exe')
    if not shell:
        raise RuntimeError('Windows PowerShell was not found on PATH.')
    # Downloaded ZIPs mark scripts as Internet-origin. This applies only to
    # the child process; no CurrentUser/LocalMachine policy is changed.
    powershell = [shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File']
    if args.setup or args.dependencies_only:
        command = [*powershell, ROOT / 'setup.ps1', '-Demo']
        if args.dependencies_only:
            command.append('-DependenciesOnly')
    elif args.benchmark:
        command = [*powershell, ROOT / 'benchmark.ps1']
    elif args.test:
        command = [*powershell, ROOT / 'launch/test.ps1']
    else:
        operation = 'Status' if args.status else 'Stop' if args.stop else 'Start'
        command = [*powershell, ROOT / 'launch/kit.ps1', '-Operation', operation]
    run(command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=('host', 'demo'), default='host')
    group = parser.add_mutually_exclusive_group()
    for flag in ('status', 'stop', 'setup', 'dependencies-only', 'test', 'benchmark'):
        group.add_argument('--' + flag, action='store_true')
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError('Python 3.11+ is required; this interpreter is ' + sys.version.split()[0])
        if args.mode == 'host':
            with host_lock():
                host(args)
        else:
            demo(args)
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'LAUNCH FAILED: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
