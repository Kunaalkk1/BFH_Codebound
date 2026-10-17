"""Bounded line protocol shared by the trusted API/helper Docker supervisors."""
import json
from queue import Queue, Empty
from threading import Thread
from time import monotonic


def exchange(process, initial, callback, *, timeout, output_limit):
    events=Queue(maxsize=4)
    def reader():
        try:
            while True:
                line=process.stdout.readline(output_limit+4097)
                if not line: break
                if len(line)>output_limit+4096:
                    events.put({'status':'failed','error':'OUTPUT_LIMIT_EXCEEDED'}); break
                try: events.put(json.loads(line))
                except ValueError: events.put({'status':'failed','error':'INVALID_JSON_RESULT'}); break
        finally: events.put(None)
    # No worker stderr is ever surfaced or logged.
    Thread(target=reader,daemon=True).start()
    process.stdin.write(json.dumps(initial,allow_nan=False).encode()+b'\n')
    process.stdin.flush()
    deadline=monotonic()+timeout
    calls=0
    while monotonic()<deadline:
        try: message=events.get(timeout=min(.05,max(.001,deadline-monotonic())))
        except Empty: continue
        if not isinstance(message,dict): raise RuntimeError('WORKER_FAILED')
        if message.get('status')=='call':
            calls+=1
            if calls>64: raise RuntimeError('CALL_LIMIT_EXCEEDED')
            if len(json.dumps(message).encode())>8192: raise RuntimeError('CALL_LIMIT_EXCEEDED')
            reply=callback(message.get('name'),message.get('arguments'))
            if monotonic()>=deadline: raise RuntimeError('TIMED_OUT')
            encoded=json.dumps(reply,allow_nan=False).encode()+b'\n'
            if len(encoded)>2_097_152: raise RuntimeError('INPUT_LIMIT_EXCEEDED')
            process.stdin.write(encoded); process.stdin.flush()
        elif message.get('status')=='succeeded':
            result=message.get('result')
            if not isinstance(result,dict) or len(json.dumps(result).encode())>output_limit: raise RuntimeError('OUTPUT_LIMIT_EXCEEDED')
            return result
        else:
            code=message.get('error')
            if code not in {'TIMED_OUT','SCRIPT_FAILED','OUTPUT_LIMIT_EXCEEDED','MEMORY_LIMIT_EXCEEDED','CALL_LIMIT_EXCEEDED','INVALID_JSON_RESULT'}: code='WORKER_FAILED'
            raise RuntimeError(code)
    raise RuntimeError('TIMED_OUT')
