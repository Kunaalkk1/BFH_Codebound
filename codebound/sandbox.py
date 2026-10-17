"""Local execution policy. This module does not provide runtime isolation."""

from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
import json
import os
from threading import BoundedSemaphore

from codebound.auth import TENANTS


class ExecutionState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class SandboxSettings:
    mode: str = "mock"
    max_concurrent_per_tenant: int = 2
    max_result_bytes: int = 16_384
    timeout_seconds: int = 5
    memory_mb: int = 128
    cpus: float = 0.5
    pids: int = 16
    worker_image: str = "codebound-worker:local"
    max_input_bytes: int = 2_097_152

    def __post_init__(self):
        if self.mode not in {"mock", "local_docker"}:
            raise ValueError("SANDBOX_MODE must be mock or local_docker")
        if not 1 <= self.max_concurrent_per_tenant <= 32:
            raise ValueError("Concurrency must be between 1 and 32")
        if not 256 <= self.max_result_bytes <= 1_048_576:
            raise ValueError("Result limit must be between 256 and 1048576 bytes")
        if not 1 <= self.timeout_seconds <= 30 or not 64 <= self.memory_mb <= 512:
            raise ValueError("Timeout must be 1..30 seconds; memory 64..512 MiB")
        if not 0.1 <= self.cpus <= 2 or not 8 <= self.pids <= 64:
            raise ValueError("CPU must be 0.1..2; PIDs 8..64")
        if not 1024 <= self.max_input_bytes <= 8_388_608:
            raise ValueError("Input cap must be 1024..8388608 bytes")

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv("SANDBOX_MODE", "mock"),
                   max_concurrent_per_tenant=int(os.getenv("SANDBOX_MAX_CONCURRENT", "2")),
                   max_result_bytes=int(os.getenv("SANDBOX_MAX_RESULT_BYTES", "16384")),
                   timeout_seconds=int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "5")),
                   memory_mb=int(os.getenv("SANDBOX_MEMORY_MB", "128")),
                   cpus=float(os.getenv("SANDBOX_CPUS", "0.5")),
                   pids=int(os.getenv("SANDBOX_PIDS", "16")),
                   worker_image=os.getenv("SANDBOX_WORKER_IMAGE", "codebound-worker:local"))


class RuntimeUnavailable(Exception):
    pass


class CapacityExceeded(Exception):
    pass


class ResultTooLarge(Exception):
    pass


class ExecutionGate:
    def __init__(self, settings: SandboxSettings):
        self.settings = settings
        self.slots = {tenant: BoundedSemaphore(settings.max_concurrent_per_tenant) for tenant in TENANTS}

    @contextmanager
    def slot(self, tenant_id: str):
        semaphore = self.slots[tenant_id]
        if not semaphore.acquire(blocking=False):
            raise CapacityExceeded()
        try:
            yield
        finally:
            semaphore.release()

    def check_result(self, result: dict):
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self.settings.max_result_bytes:
            raise ResultTooLarge()
