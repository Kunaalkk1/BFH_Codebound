"""Trusted local server runner with a file-triggered graceful shutdown."""

import json
import os
from pathlib import Path
import sys
from threading import Thread
from time import sleep

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from codebound.demo import create_demo_app


def main():
    instance = sys.argv[1]
    if not (os.getenv("CODEBOUND_DEMO_TOKEN_A") and os.getenv("CODEBOUND_DEMO_TOKEN_B")):
        tokens = json.loads((ROOT / ".local/demo-tokens.json").read_text(encoding="utf-8-sig"))
        os.environ["CODEBOUND_DEMO_TOKEN_A"] = tokens["a"]
        os.environ["CODEBOUND_DEMO_TOKEN_B"] = tokens["b"]
    os.environ["SANDBOX_MODE"] = sys.argv[2] if len(sys.argv) > 2 else "mock"
    server = uvicorn.Server(uvicorn.Config(create_demo_app(), host="127.0.0.1", port=8000))
    stop_file = ROOT / ".local" / f"stop-{instance}"

    def watch():
        while not server.should_exit:
            if stop_file.exists():
                server.should_exit = True
                return
            sleep(0.25)

    Thread(target=watch, daemon=True).start()
    try:
        server.run()
    finally:
        stop_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
