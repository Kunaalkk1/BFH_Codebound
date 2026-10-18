"""CI-only native Linux lifecycle test; Ollama inference is explicitly excluded."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import demo_launch

if os.name!='posix' or os.getenv('CI')!='true':
    raise SystemExit('This test is only for a disposable Linux CI runner.')
if (ROOT/'.local/kit/state/state.json').exists():
    raise SystemExit('Refusing a populated demo installation.')

# Reference workflows do not use generation. Normal setup still checks Ollama.
demo_launch.ollama=lambda pull=False: None
def flags(**updates):
    values=dict(setup=False,dependencies_only=False,test=False,stop=False,status=False,benchmark=False)
    values.update(updates)
    return SimpleNamespace(**values)

try:
    demo_launch.main(flags(setup=True))
    demo_launch.main(flags())
    state=(ROOT/'.local/kit/backend.json').read_text()
    demo_launch.main(flags())
    assert (ROOT/'.local/kit/backend.json').read_text()==state
    demo_launch.run([demo_launch.node(),ROOT/'external/codebound-kit/harness/cli.mjs','check',
        '--team',ROOT/'.local/kit/team.json','--only','must','--json',ROOT/'.local/kit/linux-ci-must-report.json'])
finally:
    if (ROOT/'.local/kit/state/state.json').exists():
        demo_launch.main(flags(stop=True))
