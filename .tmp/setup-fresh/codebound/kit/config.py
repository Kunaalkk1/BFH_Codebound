"""Private kit paths and fixed bindings. No expected results are loaded by runtime."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
KIT=ROOT/'external/codebound-kit'
LOCAL=ROOT/'.local/kit'
STATE=LOCAL/'state/state.json'
CONFIG=LOCAL/'integration.json'
PROGRAMS=Path(__file__).with_name('programs')
BINDINGS={
 'price_drift_check':('erp','supplier'),
 'catalogue_total':('supplier',),
 'stuck_orders':('erp','carrier'),
 'offer_chain':('supplier',),
}
SCHEMAS={
 'price_drift_check':{'threshold':{'type':'number'}},
 'catalogue_total':{'category':{'type':'string'}},
 'stuck_orders':{'days':{'type':'number'},'as_of':{'type':'string'}},
}
def state(): return json.loads(STATE.read_text(encoding='utf-8-sig'))
def config(): return json.loads(CONFIG.read_text(encoding='utf-8-sig'))
