"""Merge one Claude server without displaying existing configuration or secrets."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from uuid import uuid4


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def merge(path, python, bridge, *, preview=False):
    path, python, bridge = (Path(value).resolve() for value in (path, python, bridge))
    original = path.read_bytes() if path.exists() else None
    config = json.loads(original.decode("utf-8-sig"), object_pairs_hook=no_duplicates) if original is not None else {}
    if not isinstance(config, dict):
        raise ValueError("Config must be an object")
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be an object")
    servers["codebound-tenant-a"] = {"command": str(python), "args": ["-u", str(bridge)]}
    content = json.dumps(config, indent=2, ensure_ascii=False).encode("utf-8")
    assert json.loads(content) == config
    if preview:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if (path.read_bytes() if path.exists() else None) != original:
        raise ValueError("Config changed during preparation")
    if original is not None:
        backup = path.with_name(path.name + ".backup-" + uuid4().hex)
        shutil.copy2(path, backup)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        if (path.read_bytes() if path.exists() else None) != original:
            raise ValueError("Config changed before replacement")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    try:
        backup = merge(args.config, args.python, Path(__file__).with_name("bridge.py"), preview=args.preview)
        print("Preview validated; no files changed." if args.preview else "Configured only codebound-tenant-a; existing settings preserved.")
        if backup:
            print("Previous config backup: " + str(backup))
    except Exception:
        print("Claude config merge failed; check permissions and valid JSON. No credentials displayed.", file=sys.stderr)
        raise SystemExit(1)
