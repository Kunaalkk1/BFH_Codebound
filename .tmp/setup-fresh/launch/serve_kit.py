"""Start the isolated official-kit API with ownership-checked file shutdown."""
import os
from pathlib import Path
import sys
from threading import Thread
from time import sleep
import uvicorn
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from codebound.kit.api import create_app

def main():
    instance=sys.argv[1]
    server=uvicorn.Server(uvicorn.Config(create_app(),host='127.0.0.1',port=8001,access_log=False))
    stop=ROOT/'.local/kit'/('stop-'+instance)
    def watch():
        while not server.should_exit:
            if stop.exists(): server.should_exit=True; return
            sleep(.25)
    Thread(target=watch,daemon=True).start()
    try: server.run()
    finally: stop.unlink(missing_ok=True)
if __name__=='__main__': main()
