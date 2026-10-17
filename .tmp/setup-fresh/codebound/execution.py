"""Mock dispatch only: source is compared as text, never interpreted or executed."""

from pathlib import Path
from typing import Protocol

from codebound.datasets import september_totals
from codebound.models import ExecutionRequest

MOCK_OPERATIONS = {
    "# codebound-mock:september_totals": "september_totals",
    "# codebound-mock:ticket_totals": "ticket_totals",
}


class UnsupportedOperation(Exception):
    pass


class ConnectorNotImplemented(Exception):
    pass


class SandboxExecutor(Protocol):
    """Future adapters must enforce isolation externally, not trust Python controls.

    An adapter must use tenant-authorized references only, preserve script version
    identity, and return bounded JSON. Replacement requires a new security review.
    """

    def execute(self, *, tenant_id: str, script: dict, request: ExecutionRequest) -> dict: ...


class MockSandboxExecutor:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def execute(self, *, tenant_id: str, script: dict, request: ExecutionRequest) -> dict:
        operation = MOCK_OPERATIONS.get(script["source_code"])
        if operation is None:
            raise UnsupportedOperation()
        if request.reference.kind != "dataset":
            raise ConnectorNotImplemented()
        return september_totals(
            self.data_dir, request.reference.id, request.parameters.year,
            tenant_id=tenant_id, ticket_type=request.parameters.ticket_type,
            month=9 if operation == "september_totals" else None,
        )
