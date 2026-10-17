"""Private trusted launcher process. Never expose this stdin protocol as an API."""

import json
import sys

from codebound.datasets import DEFAULT_DATA_DIR
from codebound.docker_executor import LocalDockerExecutor, WorkerFailure
from codebound.sandbox import ResultTooLarge, SandboxSettings


def main():
    try:
        job = json.loads(sys.stdin.buffer.readline(8_388_609))
        executor = LocalDockerExecutor(DEFAULT_DATA_DIR, SandboxSettings(**job["settings"]))
        def broker(name, arguments):
            print(json.dumps({"status":"call", "name":name, "arguments":arguments}), flush=True)
            return json.loads(sys.stdin.buffer.readline(2_097_153))
        result = executor.run_job(job["payload"], broker=broker if job["payload"].get("brokered") else None)
        message = {"status": "succeeded", "result": result}
    except WorkerFailure as exc:
        message = {"status": "failed", "error": exc.code}
    except ResultTooLarge:
        message = {"status": "failed", "error": "OUTPUT_LIMIT_EXCEEDED"}
    except RuntimeError as exc:
        code = str(exc)
        if code not in {"TIMED_OUT", "SCRIPT_FAILED", "OUTPUT_LIMIT_EXCEEDED", "MEMORY_LIMIT_EXCEEDED", "CALL_LIMIT_EXCEEDED", "INVALID_JSON_RESULT"}:
            code = "WORKER_FAILED"
        message = {"status":"failed", "error":code}
    except Exception:
        message = {"status": "unavailable"}
    print(json.dumps(message, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
