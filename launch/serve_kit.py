"""Start the isolated official-kit API with ownership-checked file shutdown."""
import os
import ipaddress
import socket
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
    app=create_app()
    @app.get('/instance')
    def identity(): return {'instance':instance}
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8001,access_log=False))
    sockets=[]
    extra=os.getenv('CODEBOUND_KIT_BIND_ADDRESS')
    if extra:
        address=ipaddress.ip_address(extra)
        if os.name=='nt' or address.version!=4 or not address.is_private or address.is_loopback or address.is_unspecified:
            raise ValueError('Invalid native Docker interface binding')
        for host in ('127.0.0.1',str(address)):
            listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind((host,8001)); listener.listen(128)
            sockets.append(listener)
    stop=ROOT/'.local/kit'/('stop-'+instance)
    def watch():
        while not server.should_exit:
            if stop.exists(): server.should_exit=True; return
            sleep(.25)
    Thread(target=watch,daemon=True).start()
    try: server.run(sockets=sockets or None)
    finally:
        stop.unlink(missing_ok=True)
        for listener in sockets: listener.close()
if __name__=='__main__': main()
