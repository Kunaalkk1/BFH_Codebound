"""Runs ONLY inside the disposable Linux worker, never import into the backend."""

import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time


def emit(status, **fields):
    print(json.dumps({"status": status, **fields}, allow_nan=False, separators=(",", ":")), flush=True)


def main():
    request = json.loads(sys.stdin.buffer.readline(8_388_609))
    if request.get("brokered"):
        sys.path.insert(0, "/worker")
        from brokered import main as brokered_main
        brokered_main(request)
        return
    # All files are on this execution's size-limited tmpfs, never host mounts.
    Path("/tmp/input.json").write_text(json.dumps(request["input"]), encoding="utf-8")
    Path("/tmp/script.py").write_text(request["source"], encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-I", "-B", "/tmp/script.py"],
                             cwd="/tmp", stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
                             start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ, "stdout")
    selector.register(child.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + request["timeout"]
    error = None
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                error = "TIMED_OUT"
                break
            for key, _ in selector.select(timeout=0.05):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if sum(map(len, output.values())) + len(chunk) > request["output_limit"]:
                    error = "OUTPUT_LIMIT_EXCEEDED"
                    break
                output[key.data].extend(chunk)
            if error:
                break
        if error:
            emit("failed", error=error)
            return
        returncode = child.wait(timeout=max(0.01, deadline - time.monotonic()))
        if returncode:
            # Raw stderr can contain input rows/secrets; report a fixed error label.
            emit("failed", error="SCRIPT_FAILED")
            return
        try:
            result = json.loads(output["stdout"], parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            emit("failed", error="INVALID_JSON_RESULT")
            return
        emit("succeeded", result=result)
    except subprocess.TimeoutExpired:
        emit("failed", error="TIMED_OUT")
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        selector.close()


if __name__ == "__main__":
    main()
