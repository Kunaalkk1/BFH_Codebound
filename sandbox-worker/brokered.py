"""Brokered worker mode. Loaded only inside the network-disabled container."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

PRELUDE = '''import json as _cb_json, sys as _cb_sys
class _CBTools:
    def call(self, name, arguments=None):
        _cb_sys.stdout.write(_cb_json.dumps({"__codebound_call":True,"name":name,"arguments":arguments or {}})+"\\n")
        _cb_sys.stdout.flush()
        reply=_cb_json.loads(_cb_sys.stdin.readline())
        if "error" in reply: raise RuntimeError(reply["error"])
        return reply["value"]
tools=_CBTools()
parameters=_cb_json.load(open("/tmp/input.json"))["parameters"]
'''


def main(request):
    def emit(**value): print(json.dumps(value,allow_nan=False),flush=True)
    Path('/tmp/input.json').write_text(json.dumps(request['input']))
    Path('/tmp/script.py').write_text(PRELUDE+'\n'+request['source'])
    child=subprocess.Popen([sys.executable,'-I','-B','/tmp/script.py'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd='/tmp',env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8'},start_new_session=True)
    selector=selectors.DefaultSelector()
    selector.register(child.stdout,selectors.EVENT_READ,'out')
    selector.register(child.stderr,selectors.EVENT_READ,'err')
    deadline=time.monotonic()+request['timeout']
    buffer=bytearray(); final=bytearray(); stderr_bytes=0; calls=0
    try:
        while selector.get_map():
            if time.monotonic()>deadline: raise RuntimeError('TIMED_OUT')
            for key,_ in selector.select(.05):
                chunk=os.read(key.fileobj.fileno(),4096)
                if not chunk: selector.unregister(key.fileobj); continue
                if key.data=='err':
                    stderr_bytes+=len(chunk)
                    if stderr_bytes>request['output_limit']: raise RuntimeError('OUTPUT_LIMIT_EXCEEDED')
                    continue
                buffer.extend(chunk)
                if len(buffer)>request['output_limit']+8192: raise RuntimeError('OUTPUT_LIMIT_EXCEEDED')
                while b'\n' in buffer:
                    line,_,rest=buffer.partition(b'\n'); buffer=bytearray(rest)
                    try: value=json.loads(line)
                    except ValueError: value=None
                    if isinstance(value,dict) and value.get('__codebound_call') is True:
                        calls+=1
                        if calls>64 or len(line)>8192: raise RuntimeError('CALL_LIMIT_EXCEEDED')
                        emit(status='call',name=value.get('name'),arguments=value.get('arguments'))
                        reply=sys.stdin.buffer.readline(2_097_153)
                        if not reply or len(reply)>2_097_152: raise RuntimeError('WORKER_FAILED')
                        child.stdin.write(reply); child.stdin.flush()
                    else:
                        final.extend(line+b'\n')
                        if len(final)>request['output_limit']: raise RuntimeError('OUTPUT_LIMIT_EXCEEDED')
        if child.wait(timeout=max(.01,deadline-time.monotonic())): raise RuntimeError('SCRIPT_FAILED')
        final.extend(buffer)
        result=json.loads(final)
        if not isinstance(result,dict): raise RuntimeError('INVALID_JSON_RESULT')
        emit(status='succeeded',result=result)
    except (RuntimeError,ValueError,subprocess.TimeoutExpired) as exc:
        code=str(exc) if isinstance(exc,RuntimeError) else 'INVALID_JSON_RESULT'
        emit(status='failed',error=code)
    finally:
        try: os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait(); selector.close()
