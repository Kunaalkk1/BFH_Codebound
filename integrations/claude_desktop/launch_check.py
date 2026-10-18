"""Read-only config/DPAPI/upstream check; no configuration values are printed."""
import asyncio
import json
from pathlib import Path
import sys
from diagnose import check


def main():
    try:
        root = Path(__file__).resolve().parents[2]
        config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
        server = config['mcpServers']['codebound-tenant-a']
        expected = root / 'integrations/claude_desktop/bridge.py'
        valid = (Path(server['command']).resolve() == Path(sys.executable).resolve()
                 and server['args'] == ['-u', str(expected)])
        result = asyncio.run(check())
        result['configuration'] = valid
        print('Claude bridge: ' + json.dumps(result))
        return 0 if all(result.values()) else 1
    except Exception:
        print('Claude bridge configuration unavailable; review launch/configure-claude.ps1 -Preview.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
