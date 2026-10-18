"""Native Linux demo lifecycle. No PowerShell or Windows credentials required."""
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

import launch as shared

ROOT = shared.ROOT
LOCAL = ROOT / '.local/kit'
KIT = ROOT / 'external/codebound-kit'
STATE = LOCAL / 'backend.json'
SDK = ROOT / '.venv-claude/bin/python'


def environment():
    env = dict(os.environ)
    for key in ('COMPOSE_PROJECT_NAME', 'JWT_SECRET', 'ENCRYPTION_KEY', 'POSTGRES_PASSWORD', 'ANYTHINGMCP_IMAGE'):
        env.pop(key, None)
    env.update(CODEBOUND_STATE_DIR=str(LOCAL/'state'), CODEBOUND_MOCKS_ADMIN='http://127.0.0.1:14180',
        CODEBOUND_OUTSIDE_ADMIN='http://127.0.0.1:14181')
    model = ROOT/'.local/ollama.json'
    if not env.get('OLLAMA_MODEL'):
        env['OLLAMA_MODEL'] = json.loads(model.read_text())['model'] if model.exists() else 'qwen2.5-coder:3b'
    return env


def run(command, **kwargs):
    shared.run(command, env=environment(), **kwargs)


def module(name, *args):
    run([shared.PYTHON, '-m', name, *args])


def fetch_kit():
    pin = json.loads((ROOT/'dependencies.json').read_text())['codebound-kit']
    if not shutil.which('git'):
        raise RuntimeError('Install Git before demo setup.')
    if not KIT.exists() or not any(KIT.iterdir()):
        KIT.parent.mkdir(parents=True, exist_ok=True)
        # Public source files must remain readable when Docker COPY puts them
        # in an image owned by root but executed as the upstream node user.
        # Keep the private state umask in this process; change only Git's child.
        run(['git', 'clone', '--no-checkout', pin['url'], KIT],umask=0o022)
        run(['git', '-C', KIT, 'checkout', '--detach', pin['commit']],umask=0o022)
    actual = subprocess.check_output(['git', '-C', str(KIT), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != pin['commit'] or not (KIT/'harness/setup.mjs').is_file():
        raise RuntimeError('Existing external/codebound-kit differs from the tested pin or is incomplete; preserved for review.')


def node():
    executable = shutil.which('node')
    if not executable:
        raise RuntimeError('Demo requires Node.js 20+; install it and retry.')
    version = subprocess.check_output([executable, '--version'], text=True).strip()
    if int(version.lstrip('v').split('.')[0]) < 20:
        raise RuntimeError('Demo requires Node.js 20+; found '+version)
    return executable


def compose(executable, mocks, *options):
    files = [KIT/'compose/mocks.yml', ROOT/'docker/compose.kit-mocks.yml'] if mocks else [
        ROOT/'docker/compose.kit.yml', KIT/'compose/anythingmcp.override.yml',
        ROOT/'docker/compose.kit-network.yml', ROOT/'docker/compose.kit-linux.yml']
    command = [executable, 'compose']
    if not mocks:
        command += ['--env-file', LOCAL/'compose.env']
    for file in files:
        command += ['-f', file]
    run([*command, *options])


def wait(url, seconds=40):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if shared.probe(url):
            return
        time.sleep(.5)
    raise RuntimeError('Readiness timed out: '+url)


def ollama(pull=False):
    executable = os.environ.get('CODEBOUND_OLLAMA_EXE') or shutil.which('ollama')
    if not shared.probe('http://127.0.0.1:11434/api/version'):
        if not executable:
            raise RuntimeError('Install/start native Ollama for demo mode, or set CODEBOUND_OLLAMA_EXE.')
        with (LOCAL/'ollama.out.log').open('ab') as output:
            subprocess.Popen([executable, 'serve'], stdin=subprocess.DEVNULL, stdout=output,
                stderr=output, start_new_session=True, env=environment())
        wait('http://127.0.0.1:11434/api/version', 30)
    if pull:
        if not executable:
            raise RuntimeError('Ollama API is running, but its CLI is missing; install it to pull the demo model.')
        run([executable, 'pull', environment()['OLLAMA_MODEL']])
        config = ROOT/'.local/ollama.json'
        if not config.exists():
            config.write_text(json.dumps({'model': environment()['OLLAMA_MODEL']}))
    module('codebound.llm.check')


def prepare(executable):
    config = LOCAL/'compose.env'
    if not config.exists():
        volumes = subprocess.check_output([executable, 'volume', 'ls', '-q', '--filter',
            'label=com.docker.compose.project=codebound-kit'], text=True).strip()
        if volumes:
            raise RuntimeError('Existing kit volume without .local/kit/compose.env. Restore its original secrets before setup.')
    module('codebound.kit.prepare')
    content = config.read_text()
    if not any(line.startswith('ANYTHINGMCP_IMAGE=') for line in content.splitlines()):
        image = json.loads((ROOT/'dependencies.json').read_text())['anythingmcp-image']
        with config.open('a') as output:
            output.write('\nANYTHINGMCP_IMAGE='+image+'\n')
    os.chmod(config, 0o600)


def setup(args):
    shared.dependencies()
    node()
    fetch_kit()
    if args.dependencies_only:
        print('Linux demo source and backend dependencies ready. Run python3 launch.py demo --setup to provision.')
        return
    executable = shared.docker()
    ollama(pull=True)
    prepare(executable)
    compose(executable, True, 'up', '-d', '--build')
    wait('http://127.0.0.1:14180/__health')
    wait('http://127.0.0.1:14181/__health')
    compose(executable, False, 'up', '-d', '--wait', '--wait-timeout', '240')
    if not (LOCAL/'state/state.json').exists():
        count = subprocess.check_output([executable, 'exec', 'codebound-kit-postgres', 'psql', '-U', 'amcp',
            '-d', 'anythingmcp', '-At', '-c', 'SELECT count(*) FROM users'], text=True).strip()
        if count != '0' or shared.read('http://127.0.0.1:14180/__tenants'):
            raise RuntimeError('Demo database or mock credentials already exist without setup state; refusing to overwrite.')
        run([node(), ROOT/'launch/setup-kit.mjs'])
    run([executable, 'build', '-t', 'codebound-worker:kit', ROOT/'sandbox-worker'])
    module('codebound.kit.configure')
    print('Linux demo provisioned. Run python3 launch.py demo. Client JSON: python3 configure_claude.py --demo')


def bridge_address(executable):
    # Resolve Docker's actual host-gateway mapping rather than assuming docker0's IP.
    raw = subprocess.check_output([executable, 'exec', 'codebound-kit-app', 'node', '-e',
        "require('dns').lookup('host.docker.internal',{family:4},(e,a)=>{if(e)process.exit(1);console.log(a)})"], text=True).strip()
    address = ipaddress.ip_address(raw)
    if address.version != 4 or not address.is_private or address.is_unspecified or address.is_loopback:
        raise RuntimeError('Unrecognized Docker host-gateway address; Linux demo requires a local rootful Docker Engine.')
    return str(address)


def stop_api():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    live = shared.probe('http://127.0.0.1:8001/instance')
    if live and live.get('instance') != state['instance']:
        raise RuntimeError('Port 8001 is managed elsewhere; leaving it untouched.')
    (LOCAL/('stop-'+state['instance'])).touch()
    for _ in range(80):
        if not shared.probe('http://127.0.0.1:8001/instance'):
            STATE.unlink(missing_ok=True)
            return
        time.sleep(.25)
    raise RuntimeError('Demo API stop timed out; inspect .local/kit/backend.err.log')


def start_api(executable):
    live = shared.probe('http://127.0.0.1:8001/instance')
    if live:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        if live.get('instance') != state.get('instance'):
            raise RuntimeError('Port 8001 is managed elsewhere; leaving it untouched.')
        if state.get('source_digest') == shared.source_digest():
            return
        stop_api()
    if shared.probe('http://127.0.0.1:8001/health'):
        raise RuntimeError('Port 8001 is occupied by an unmanaged API; leaving it untouched.')
    instance = secrets.token_hex(16)
    env = environment()
    env['CODEBOUND_KIT_BIND_ADDRESS'] = bridge_address(executable)
    with (LOCAL/'backend.out.log').open('ab') as out, (LOCAL/'backend.err.log').open('ab') as err:
        process = subprocess.Popen([str(shared.PYTHON), str(ROOT/'launch/serve_kit.py'), instance],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
    STATE.write_text(json.dumps({'pid': process.pid, 'instance': instance, 'source_digest': shared.source_digest()}))
    deadline = time.monotonic()+45
    while time.monotonic() < deadline:
        live = shared.probe('http://127.0.0.1:8001/instance')
        if live and live.get('instance') == instance and shared.probe('http://127.0.0.1:8001/health'):
            return
        if process.poll() is not None:
            raise RuntimeError('Demo API exited; inspect .local/kit/backend.err.log')
        time.sleep(.5)
    raise RuntimeError('Demo API readiness timed out; inspect .local/kit/backend.err.log')


def main(args):
    import fcntl
    LOCAL.mkdir(parents=True, exist_ok=True)
    os.chmod(LOCAL, 0o700)
    previous = os.umask(0o077)
    lock=(LOCAL/'operation.lock').open('a+b')
    try:
        try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError: raise RuntimeError('Another demo operation is running; wait for it to finish.') from None
        if args.setup or args.dependencies_only:
            return setup(args)
        if args.test:
            shared.dependencies()
            return run([shared.PYTHON, '-m', 'pytest', '-q', '-m', 'not docker'])
        if not (LOCAL/'state/state.json').exists():
            raise RuntimeError('Run python3 launch.py demo --setup first. No demo state was found.')
        executable = shared.docker()
        if args.stop:
            stop_api()
            compose(executable, False, 'stop', '--timeout', '20')
            compose(executable, True, 'stop', '--timeout', '20')
            print('Demo stopped; all volumes, credentials and other installations retained.')
            return
        if args.benchmark:
            return module('codebound.kit.benchmark')
        if not args.status:
            ollama()
            compose(executable, True, 'up', '-d', '--build')
            wait('http://127.0.0.1:14180/__health')
            compose(executable, False, 'up', '-d', '--wait', '--wait-timeout', '240')
            start_api(executable)
        live = shared.read('http://127.0.0.1:8001/instance')
        state = json.loads(STATE.read_text())
        if live.get('instance') != state['instance']:
            raise RuntimeError('Demo API ownership could not be verified')
        shared.read('http://127.0.0.1:8001/health')
        module('codebound.kit.check')
        print('DEMO READY | AnythingMCP http://localhost:3100 | Codebound http://127.0.0.1:8001/docs')
    finally:
        lock.close()
        os.umask(previous)
