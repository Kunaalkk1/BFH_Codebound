"""Operator health/warm-up helper, never exposed as an unrestricted proxy."""

import argparse
import json
from codebound.llm.client import AIError, OllamaClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--warm", action="store_true")
    args = parser.parse_args()
    client = OllamaClient()
    try:
        model = client.ready()
        version = client.request("GET", "/api/version")
        print(json.dumps({"status": "ok", "version": version.get("version"), **model}))
        if args.warm:
            client.request("POST", "/api/generate", {"model": model["model"], "prompt": "Hi", "stream": False,
                           "keep_alive": "10m", "options": {"num_predict": 1, "num_ctx": 4096}}, timeout=24)
            print("Local model warm-up completed; no script executed.")
    except AIError as exc:
        print(exc.code + ": install/start native Ollama, pull one local model and configure OLLAMA_MODEL.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
