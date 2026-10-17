"""Private trusted launcher process. Never expose this stdin protocol as an API."""

import json
import sys

from codebound.datasets import DEFAULT_DATA_DIR
from codebound.docker_executor import LocalDockerExecutor, WorkerFailure
from codebound.sandbox import ResultTooLarge, SandboxSettings


def main():
    try:
        job = json.load(sys.stdin)
        executor = LocalDockerExecutor(DEFAULT_DATA_DIR, SandboxSettings(**job["settings"]))
        result = executor.run_job(job["payload"])
        message = {"status": "succeeded", "result": result}
    except WorkerFailure as exc:
        message = {"status": "failed", "error": exc.code}
    except ResultTooLarge:
        message = {"status": "failed", "error": "OUTPUT_LIMIT_EXCEEDED"}
    except Exception:
        message = {"status": "unavailable"}
    print(json.dumps(message, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
