"""Owned local host API process with file-based graceful shutdown."""
from pathlib import Path
import sys
from threading import Thread
from time import sleep
import uvicorn
from codebound.host.api import create_app


def main():
    instance = sys.argv[1]
    app = create_app()
    @app.get('/instance')
    def identity():
        return {'instance': instance}
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8002, access_log=False))
    stop = Path(__file__).resolve().parents[2] / '.local/host' / ('stop-' + instance)
    def watch():
        while not server.should_exit:
            if stop.exists():
                server.should_exit = True
                return
            sleep(.25)
    Thread(target=watch, daemon=True).start()
    try:
        server.run()
    finally:
        stop.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
