"""Trusted Docker helper transport shared by host and demo runtimes."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

from codebound.broker_protocol import exchange

ROOT = Path(__file__).resolve().parents[1]


def execute(source, parameters, broker, metrics, *, settings, sanitize=lambda value: value):
    job = {'settings': asdict(settings), 'payload': {
        'source': source, 'input': {'parameters': parameters},
        'timeout': settings.timeout_seconds, 'output_limit': settings.max_result_bytes, 'brokered': True}}
    if len(json.dumps(job).encode()) > settings.max_input_bytes:
        raise RuntimeError('INPUT_LIMIT_EXCEEDED')
    started = perf_counter()
    process = subprocess.Popen([sys.executable, '-m', 'codebound.docker_job'], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    try:
        metrics['docker_attempts'] = metrics.get('docker_attempts', 0) + 1
        result = exchange(process, job, broker, timeout=25, output_limit=settings.max_result_bytes)
        metrics['docker_executions'] = metrics.get('docker_executions', 0) + 1
        return json.loads(sanitize(json.dumps(result, allow_nan=False)))
    finally:
        metrics['docker_ms'] = round((perf_counter() - started) * 1000, 3)
        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            raise RuntimeError('HELPER_CLEANUP_TIMEOUT') from None
