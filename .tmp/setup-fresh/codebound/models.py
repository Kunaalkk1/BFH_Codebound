"""Bounded request contracts. Client-owned tenant/version fields are forbidden."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from codebound.sandbox import ExecutionState


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)


class ScriptWrite(StrictModel):
    name: str = Field(min_length=1, max_length=120, pattern=r".*\S.*")
    source_code: str = Field(min_length=1, max_length=32_000)


class ScriptRecord(ScriptWrite):
    script_id: str
    tenant_id: str
    created_at: str
    modified_at: str
    version: int


class Reference(StrictModel):
    kind: Literal["dataset", "connector"] = "dataset"
    id: str = Field(min_length=1, max_length=120)


class InputParameters(StrictModel):
    year: int = Field(default=2026, ge=1, le=9998)
    month: int = Field(default=9, ge=1, le=12)
    ticket_type: Literal["standard", "first_class", "flex"] | None = None


class ExecutionRequest(StrictModel):
    parameters: InputParameters = Field(default_factory=InputParameters)
    reference: Reference


class ExecutionError(StrictModel):
    code: str
    message: str


class ExecutionResponse(StrictModel):
    execution_id: str
    execution_mode: str = "mock"
    status: Literal["succeeded", "failed", "denied"]
    state: ExecutionState
    result: dict | None
    duration_ms: float
    error: ExecutionError | None
