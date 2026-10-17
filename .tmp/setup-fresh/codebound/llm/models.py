from pydantic import Field
from codebound.models import StrictModel, Reference, InputParameters, ExecutionResponse


class GenerateRequest(StrictModel):
    task: str = Field(min_length=1, max_length=2000, pattern=r"[\s\S]*\S[\s\S]*")
    reference: Reference
    parameters: InputParameters = Field(default_factory=InputParameters)


class ModifyRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=2000, pattern=r"[\s\S]*\S[\s\S]*")
    expected_version: int | None = Field(default=None, ge=1)


class GeneratedSource(StrictModel):
    source_code: str = Field(min_length=1, max_length=32000)


class GenerationResponse(StrictModel):
    script_id: str
    version: int
    status: str = "saved"
    model: str
    script_reused: bool = False
    ollama_called: bool = True
    generation_ms: float = 0


class TaskResponse(GenerationResponse):
    execution: ExecutionResponse
