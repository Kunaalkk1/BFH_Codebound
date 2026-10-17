"""Trusted Docker CLI launcher; submitted Python is never a host command.

Docker daemon access is privileged infrastructure authority. No socket, host
environment, application files or credentials are passed to worker containers.
"""

import csv
import json
import os
from pathlib import Path
import shutil
import hashlib
import subprocess
import sys
from dataclasses import asdict
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

from codebound.datasets import DATASETS, DatasetUnavailableError
from codebound.sandbox import ResultTooLarge, RuntimeUnavailable, SandboxSettings

LABEL = "codebound.worker=local-v1"
PROJECT_ID = hashlib.sha256(str(Path(__file__).resolve().parent.parent).casefold().encode()).hexdigest()[:16]
PROJECT_LABEL = "codebound.project=" + PROJECT_ID


class WorkerFailure(Exception):
    def __init__(self, code: str):
        self.code = code


def docker_executable() -> str:
    candidates = [shutil.which("docker"),
                  str(Path(os.getenv("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe"),
                  str(Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Docker/Docker/resources/bin/docker.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeUnavailable()


class LocalDockerExecutor:
    def __init__(self, data_dir: Path, settings: SandboxSettings, connector_broker=None):
        self.data_dir, self.settings = data_dir, settings
        self.connector_broker = connector_broker

    def command(self, *args, timeout=15):
        # Only trusted, fixed Docker subcommands/options here, never a shell.
        try:
            result = subprocess.run([docker_executable(), *args], capture_output=True,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeUnavailable() from exc
        if result.returncode:
            raise RuntimeUnavailable()
        return result.stdout

    def ready(self):
        info = json.loads(self.command("info", "--format", "{{json .}}"))
        if info.get("OSType") != "linux" or not any("seccomp" in item and "builtin" in item for item in info.get("SecurityOptions", [])):
            raise RuntimeUnavailable()
        if any(info.get(key) is not True for key in ("MemoryLimit", "PidsLimit", "CpuCfsQuota", "SwapLimit")):
            raise RuntimeUnavailable()
        image = json.loads(self.command("image", "inspect", self.settings.worker_image))[0]
        if image["Config"].get("Volumes"):
            raise RuntimeUnavailable()
        return image["Id"]

    def authorized_input(self, tenant_id, request):
        from codebound.execution import ConnectorNotImplemented
        if request.reference.kind == "connector" and self.connector_broker is not None:
            return {"records": self.connector_broker.fetch(tenant_id, request.reference.id),
                    "parameters": request.parameters.model_dump()}
        if request.reference.kind != "dataset":
            raise ConnectorNotImplemented()
        if tenant_id not in DATASETS or request.reference.id != DATASETS[tenant_id][0]:
            raise KeyError("Dataset not found")
        records, size = [], 0
        try:
            with (self.data_dir / "railway.csv").open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                if not {"tenant_id", "travel_date", "price_cents"}.issubset(reader.fieldnames or []):
                    raise DatasetUnavailableError()
                for row in reader:
                    if row["tenant_id"] == tenant_id:
                        size += len(json.dumps(row).encode())
                        if size > self.settings.max_input_bytes:
                            raise WorkerFailure("INPUT_LIMIT_EXCEEDED")
                        records.append(row)
        except (ValueError, TypeError, csv.Error) as exc:
            raise DatasetUnavailableError() from exc
        return {"records": records, "parameters": request.parameters.model_dump()}

    def execute(self, *, tenant_id: str, script: dict, request) -> dict:
        from codebound.metrics import execution_metrics
        measurements = execution_metrics.get()
        if script["tenant_id"] != tenant_id:
            raise KeyError("Script not found")
        fetch_start = monotonic()
        data = self.authorized_input(tenant_id, request)
        if measurements is not None:
            measurements["data_fetch_ms"] = round((monotonic() - fetch_start) * 1000, 3)
            measurements["input_json_bytes"] = len(json.dumps(data["records"]).encode())
            measurements["input_json_canonical_bytes"] = len(json.dumps(data["records"], ensure_ascii=False, separators=(",", ":")).encode())
        payload = {"source": script["source_code"], "input": data,
                   "timeout": self.settings.timeout_seconds,
                   "output_limit": self.settings.max_result_bytes}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > self.settings.max_input_bytes:
            raise WorkerFailure("INPUT_LIMIT_EXCEEDED")
        # A fixed trusted helper owns the deadline/cleanup even if the API dies.
        # This subprocess never interprets the supplied source on the host.
        worker_start = monotonic()
        try:
            response = subprocess.run([sys.executable, "-m", "codebound.docker_job"],
                input=json.dumps({"settings": asdict(self.settings), "payload": payload}).encode(),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            message = json.loads(response.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise RuntimeUnavailable() from exc
        if message.get("status") == "unavailable":
            raise RuntimeUnavailable()
        if message.get("error") == "OUTPUT_LIMIT_EXCEEDED":
            raise ResultTooLarge()
        if message.get("status") != "succeeded":
            raise WorkerFailure(message.get("error", "WORKER_FAILED"))
        if measurements is not None:
            measurements["docker_ms"] = round((monotonic() - worker_start) * 1000, 3)
            measurements["docker_executions"] = measurements.get("docker_executions", 0) + 1
        return message["result"]

    def run_job(self, payload: dict, broker=None) -> dict:
        """Trusted helper entry point; its stdin is supplied only by this backend."""
        image = self.ready()
        settings = self.settings
        name = "codebound-job-" + uuid4().hex
        payload = json.dumps(payload, ensure_ascii=False).encode()
        if len(payload) > settings.max_input_bytes:
            raise WorkerFailure("INPUT_LIMIT_EXCEEDED")
        args = ["create", "--name", name, "--label", LABEL, "--label", PROJECT_LABEL, "--network", "none", "--read-only",
                "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
                "--memory", f"{settings.memory_mb}m", "--memory-swap", f"{settings.memory_mb}m",
                "--cpus", str(settings.cpus), "--pids-limit", str(settings.pids),
                "--ipc", "private", "--cgroupns", "private", "--shm-size", "1m",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                "--ulimit", "nofile=64:64", "--ulimit", "core=0:0",
                "--log-driver", "none", "--restart", "no", "-i", image]
        process = None
        try:
            self.command(*args)
            config = json.loads(self.command("inspect", name))[0]
            host = config["HostConfig"]
            # Inspect before starting. Fail closed on policy drift or injected mounts.
            if not (host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
                    and not host["Privileged"] and not config["Mounts"]
                    and host["Memory"] == settings.memory_mb * 1024**2
                    and host["MemorySwap"] == host["Memory"] and host["PidsLimit"] == settings.pids
                    and host["NanoCpus"] == int(settings.cpus * 10**9)
                    and host["CapDrop"] == ["ALL"] and config["Config"]["User"] == "10001:10001"
                    and "no-new-privileges=true" in host["SecurityOpt"]
                    and host["PidMode"] == "" and host["IpcMode"] == "private"
                    and host["CgroupnsMode"] == "private"
                    and host["Tmpfs"] == {"/tmp": "rw,noexec,nosuid,nodev,size=16m,mode=1777"}
                    and host["LogConfig"]["Type"] == "none"):
                raise RuntimeUnavailable()
            process = subprocess.Popen([docker_executable(), "start", "--attach", "-i", name],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if broker is not None:
                from codebound.broker_protocol import exchange
                return exchange(process, json.loads(payload), broker, timeout=settings.timeout_seconds + 3,
                                output_limit=settings.max_result_bytes)
            output = bytearray()
            exceeded = Event()
            # Even a compromised worker protocol cannot flood host capture memory.
            limit = settings.max_result_bytes + 4096
            def drain(stream, keep):
                while chunk := stream.read(4096):
                    if keep and len(output) + len(chunk) <= limit:
                        output.extend(chunk)
                    else:
                        exceeded.set()
            readers = [Thread(target=drain, args=(process.stdout, True), daemon=True),
                       Thread(target=drain, args=(process.stderr, False), daemon=True)]
            for thread in readers:
                thread.start()
            def deliver():
                try:
                    process.stdin.write(payload)
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            writer = Thread(target=deliver, daemon=True)
            writer.start()
            deadline = monotonic() + settings.timeout_seconds + 3
            while process.poll() is None:
                if exceeded.is_set():
                    raise ResultTooLarge()
                if monotonic() >= deadline:
                    raise WorkerFailure("TIMED_OUT")
                sleep(0.02)
            for thread in readers:
                thread.join(timeout=2)
            if exceeded.is_set():
                raise ResultTooLarge()
            state = json.loads(self.command("inspect", name))[0]["State"]
            if state.get("OOMKilled"):
                raise WorkerFailure("MEMORY_LIMIT_EXCEEDED")
            try:
                message = json.loads(output)
            except (ValueError, UnicodeError):
                raise WorkerFailure("WORKER_FAILED") from None
            if message.get("status") != "succeeded":
                code = message.get("error")
                if code == "OUTPUT_LIMIT_EXCEEDED":
                    raise ResultTooLarge()
                if code not in {"TIMED_OUT", "SCRIPT_FAILED", "INVALID_JSON_RESULT"}:
                    code = "WORKER_FAILED"
                raise WorkerFailure(code)
            result = message.get("result")
            if not isinstance(result, dict):
                raise WorkerFailure("INVALID_JSON_RESULT")
            return result
        finally:
            # Exact random name; no global prune or tenant-selected Docker target.
            self.command("rm", "--force", name)
            if process:
                if process.poll() is None:
                    process.kill()  # Docker CLI only; submitted source never runs here.
                process.wait(timeout=5)
