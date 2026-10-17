r"""Live loopback HTTP smoke test of trusted application code; no submitted code runs.

Run with .venv\Scripts\python.exe scripts/verify_http.py from the project root.
Uses disposable synthetic data, SQLite and opaque in-memory test credentials.
"""

import csv
import json
from pathlib import Path
import secrets
import socket
import tempfile
from threading import Thread
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import uvicorn

from codebound.auth import Principal
from codebound.legacy.generate_data import generate_dataset
from codebound.main import create_app


def main():
    with tempfile.TemporaryDirectory(prefix="codebound-http-") as directory:
        data_dir = Path(directory)
        generate_dataset(data_dir)
        token_a, token_b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        app = create_app(data_dir, mock_tokens={token_a: Principal("demo-tenant-1"),
                                               token_b: Principal("demo-tenant-2")})
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        checks = []

        def call(method, path, expected, token=token_a, body=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            request = Request(f"http://127.0.0.1:{port}" + path, method=method,
                              headers=headers, data=json.dumps(body).encode() if body is not None else None)
            try:
                response = urlopen(request, timeout=5)
            except HTTPError as exc:
                response = exc
            payload = response.read()
            assert response.status == expected, (method, path, response.status)
            checks.append({"method": method, "path": path, "status": response.status, "bytes": len(payload)})
            return json.loads(payload) if payload else None

        try:
            deadline = monotonic() + 10
            while not server.started:
                if not thread.is_alive() or monotonic() > deadline:
                    raise RuntimeError("Live server failed to start")
                sleep(0.05)
            source = {"name": "Live September demo", "source_code": "# codebound-mock:september_totals"}
            script = call("POST", "/scripts", 201, body=source)
            path = "/scripts/" + script["script_id"]
            assert call("GET", path, 200) == script
            assert call("GET", "/scripts", 200) == [script]
            assert call("PUT", path, 200, body=source)["version"] == 2
            execute = {"reference": {"id": "railway-demo"}, "parameters": {"year": 2026}}
            outcome = call("POST", path + "/execute", 200, body=execute)
            assert outcome["status"] == "succeeded"
            assert call("GET", "/scripts", 200, token_b) == []
            call("GET", path, 404, token_b)
            call("PUT", path, 404, token_b, source)
            call("DELETE", path, 404, token_b)
            call("POST", path + "/execute", 404, token_b, execute)
            assert call("GET", "/datasets", 200, token_b)["datasets"][0]["id"] == "lufthansa-demo"
            call("GET", "/datasets/lufthansa-demo/aggregations/september", 404)
            call("GET", "/scripts", 401, token=None)
            bad_script = call("POST", "/scripts", 201, body={**source, "source_code": "print('never run')"})
            bad_path = "/scripts/" + bad_script["script_id"]
            failed = call("POST", bad_path + "/execute", 422, body=execute)
            assert failed["error"]["code"] == "UNSUPPORTED_OPERATION"
            call("POST", path + "/execute", 501,
                 body={"reference": {"kind": "connector", "id": "unconfigured"}})
            b_script = call("POST", "/scripts", 201, token_b, source)
            b_outcome = call("POST", f"/scripts/{b_script['script_id']}/execute", 200,
                             token_b, {"reference": {"id": "lufthansa-demo"}})
            with (data_dir / "railway.csv").open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            for tenant, result in (("demo-tenant-1", outcome["result"]),
                                   ("demo-tenant-2", b_outcome["result"])):
                matching = [r for r in rows if r["tenant_id"] == tenant
                            and r["travel_date"].startswith("2026-09-")]
                assert result["ticket_count"] == len(matching) == 45
                assert result["total_revenue_cents"] == sum(int(r["price_cents"]) for r in matching)
            call("DELETE", path, 204)
            call("GET", path, 404)
            call("GET", "/openapi.json", 200, token=None)
            with app.state.store.connection() as db:
                events = db.execute("SELECT status,COUNT(*) AS count FROM execution_audit GROUP BY status").fetchall()
            print(json.dumps({"checks_passed": len(checks), "checks": checks,
                              "tenant_a_result": outcome["result"], "tenant_b_result": b_outcome["result"],
                              "audit_status_counts": {r["status"]: r["count"] for r in events}}, indent=2))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            if thread.is_alive():
                raise RuntimeError("Live server did not stop")


if __name__ == "__main__":
    main()
