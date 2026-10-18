"""Authenticated AI orchestration; reuses existing execution authorization/audit."""

import json
import sqlite3
from time import perf_counter
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from codebound.datasets import DatasetUnavailableError
from codebound.llm.client import AIError
from codebound.llm.models import GenerateRequest, ModifyRequest, GenerationResponse, TaskResponse
from codebound.metrics import execution_metrics
from codebound.models import ExecutionRequest
from codebound.scripts import Tenant, execute_saved

router = APIRouter(prefix="/ai", tags=["local AI"])


@router.get("/health")
def health(request: Request, tenant: Tenant):
    try:
        model = request.app.state.ai.client.ready()
        return {"status": "ok", "inference": "local", **model, "automatic_repair": False}
    except AIError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from None


def operation(name, body, request, tenant, script_id=None):
    service = request.app.state.ai
    started = perf_counter()
    metrics = {"ollama_calls": 0, "ollama_api_requests": 0, "generation_ms": 0,
               "broker_mcp_rpc_attempts": 0, "broker_tool_calls": 0,
               "postgres_schema_calls": 0, "postgres_data_calls": 0, "docker_executions": 0,
               "script_reused": False, "repair_attempts": 0}
    status, result, record = 200, None, None
    token = execution_metrics.set(metrics)
    acquired = False
    try:
        if name == "run" and request.app.state.sandbox_settings.mode != "local_docker":
            raise AIError("REAL_DOCKER_REQUIRED", 409)
        acquired = service.locks[tenant.tenant_id].acquire(blocking=False)
        if not acquired:
            raise AIError("TENANT_AI_BUSY", 429)
        if name == "modify":
            record, model = service.modify(tenant.tenant_id, str(script_id), body, metrics)
        else:
            record, model = service.generate(tenant.tenant_id, body, metrics, reuse=name == "run")
        result = {"script_id": record["script_id"], "version": record["version"], "status": "saved",
                  "model": model, "script_reused": metrics["script_reused"],
                  "ollama_called": bool(metrics["ollama_calls"]), "generation_ms": metrics.get("generation_ms", 0)}
        if name == "run":
            response = execute_saved(UUID(record["script_id"]),
                ExecutionRequest(reference=body.reference, parameters=body.parameters), request, tenant,
                expected_version=record["version"])
            status = response.status_code
            result["execution"] = json.loads(response.body)
            result["status"] = result["execution"]["status"]
    except AIError as exc:
        status, result = exc.status, {"error": {"code": exc.code, "message": "Local AI operation could not be completed"}}
    except DatasetUnavailableError:
        status, result = 503, {"error": {"code": "SCHEMA_UNAVAILABLE", "message": "Authorized schema unavailable"}}
    except Exception:
        status, result = 503, {"error": {"code": "AI_OPERATION_FAILED", "message": "Local AI operation unavailable"}}
    finally:
        if acquired:
            service.locks[tenant.tenant_id].release()
        execution_metrics.reset(token)
    metrics["total_ms"] = round((perf_counter() - started) * 1000, 3)
    metrics["response_json_bytes"] = len(json.dumps(result, separators=(",", ":")).encode())
    try:
        service.store.event(tenant.tenant_id, name, record["script_id"] if record else str(script_id) if script_id else None,
                            "succeeded" if status < 400 else "failed", metrics)
    except sqlite3.Error:
        return JSONResponse(status_code=503, content={"error": {"code": "AI_AUDIT_UNAVAILABLE", "message": "AI audit unavailable"}})
    return JSONResponse(status_code=status, content=result)


@router.post("/scripts/generate", response_model=GenerationResponse)
def generate(body: GenerateRequest, request: Request, tenant: Tenant):
    return operation("generate", body, request, tenant)


@router.post("/scripts/{script_id}/modify", response_model=GenerationResponse)
def modify(script_id: UUID, body: ModifyRequest, request: Request, tenant: Tenant):
    return operation("modify", body, request, tenant, script_id)


@router.post("/tasks/run", response_model=TaskResponse)
def run(body: GenerateRequest, request: Request, tenant: Tenant):
    return operation("run", body, request, tenant)
