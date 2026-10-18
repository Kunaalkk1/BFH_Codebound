"""Authenticated script API and execution orchestration; contains no code evaluator."""

from time import perf_counter
import sqlite3
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from codebound.auth import Principal, authenticated_tenant
from codebound.datasets import DATASETS, DatasetUnavailableError
from codebound.execution import ConnectorNotImplemented, MOCK_OPERATIONS, UnsupportedOperation
from codebound.models import ExecutionRequest, ExecutionResponse, ScriptRecord, ScriptWrite
from codebound.sandbox import CapacityExceeded, ExecutionState, ResultTooLarge, RuntimeUnavailable
from codebound.docker_executor import WorkerFailure

router = APIRouter(prefix="/scripts", tags=["scripts"])
Tenant = Annotated[Principal, Depends(authenticated_tenant)]


@router.post("", status_code=201, response_model=ScriptRecord)
def create(body: ScriptWrite, request: Request, tenant: Tenant):
    return request.app.state.store.create(tenant.tenant_id, body.name, body.source_code)


@router.get("", response_model=list[ScriptRecord])
def listing(request: Request, tenant: Tenant, limit: int = Query(100, ge=1, le=100),
            offset: int = Query(0, ge=0)):
    return request.app.state.store.list(tenant.tenant_id, limit, offset)


@router.get("/{script_id}", response_model=ScriptRecord)
def retrieve(script_id: UUID, request: Request, tenant: Tenant):
    script = request.app.state.store.get(tenant.tenant_id, str(script_id))
    if script is None:
        raise HTTPException(404, "Script not found")
    return script


@router.put("/{script_id}", response_model=ScriptRecord)
def update(script_id: UUID, body: ScriptWrite, request: Request, tenant: Tenant):
    script = request.app.state.store.update(tenant.tenant_id, str(script_id), body.name, body.source_code)
    if script is None:
        raise HTTPException(404, "Script not found")
    return script


@router.delete("/{script_id}", status_code=204)
def delete(script_id: UUID, request: Request, tenant: Tenant):
    if not request.app.state.store.delete(tenant.tenant_id, str(script_id)):
        raise HTTPException(404, "Script not found")
    return Response(status_code=204)


@router.post("/{script_id}/execute", response_model=ExecutionResponse,
             responses={404: {"model": ExecutionResponse}, 422: {},
                        413: {"model": ExecutionResponse}, 429: {"model": ExecutionResponse},
                        501: {"model": ExecutionResponse}, 503: {"model": ExecutionResponse}})
def execute(script_id: UUID, body: ExecutionRequest, request: Request, tenant: Tenant):
    return execute_saved(script_id, body, request, tenant)


def execute_saved(script_id, body, request, tenant, *, expected_version=None):
    start = perf_counter()
    execution_id = str(uuid4())
    store = request.app.state.store
    script = store.get(tenant.tenant_id, str(script_id))
    status, http_status, result, error = "succeeded", 200, None, None
    state = ExecutionState.PENDING
    mode = request.app.state.sandbox_settings.mode
    operation = ("python" if mode == "local_docker" else MOCK_OPERATIONS.get(script["source_code"], "unsupported")) if script else "unresolved"
    if script is None:
        state = ExecutionState.REJECTED
        status, http_status = "denied", 404
        error = {"code": "SCRIPT_NOT_FOUND", "message": "Script not found"}
    elif expected_version is not None and script["version"] != expected_version:
        state = ExecutionState.REJECTED
        status, http_status = "denied", 409
        error = {"code": "SCRIPT_VERSION_CONFLICT", "message": "Script changed before execution"}
    elif body.reference.kind == "dataset" and body.reference.id != DATASETS[tenant.tenant_id][0]:
        state = ExecutionState.REJECTED
        status, http_status = "denied", 404
        error = {"code": "DATASET_NOT_FOUND", "message": "Dataset not found"}
    else:
        try:
            with request.app.state.execution_gate.slot(tenant.tenant_id):
                state = ExecutionState.RUNNING
                result = request.app.state.executor.execute(tenant_id=tenant.tenant_id, script=script, request=body)
                request.app.state.execution_gate.check_result(result)
                state = ExecutionState.COMPLETED
        except RuntimeUnavailable:
            status, http_status = "failed", 503
            error = {"code": "RUNTIME_UNAVAILABLE", "message": "Configured execution runtime unavailable"}
        except WorkerFailure as exc:
            status, http_status, result = "failed", 422, None
            if exc.code == "TIMED_OUT":
                state, http_status = ExecutionState.TIMED_OUT, 408
            error = {"code": exc.code, "message": "Worker execution failed or exceeded configured limits"}
        except CapacityExceeded:
            state = ExecutionState.REJECTED
            status, http_status = "denied", 429
            error = {"code": "TENANT_CAPACITY_EXCEEDED", "message": "Tenant execution limit reached"}
        except ResultTooLarge:
            status, http_status, result = "failed", 413, None
            error = {"code": "OUTPUT_LIMIT_EXCEEDED", "message": "Result exceeds the configured byte limit"}
        except UnsupportedOperation:
            state = ExecutionState.REJECTED
            status, http_status = "failed", 422
            error = {"code": "UNSUPPORTED_OPERATION", "message": "Only exact mock source markers are supported"}
        except ConnectorNotImplemented:
            status, http_status = "failed", 501
            error = {"code": "CONNECTOR_NOT_IMPLEMENTED", "message": "Connector integration is not implemented"}
        except (KeyError, FileNotFoundError):
            status, http_status = "failed", 404
            error = {"code": "DATASET_NOT_FOUND", "message": "Dataset not found"}
        except DatasetUnavailableError:
            status, http_status = "failed", 503
            error = {"code": "DATASET_UNAVAILABLE", "message": "Dataset unavailable"}
        except Exception:
            status, http_status, result = "failed", 503, None
            error = {"code": "EXECUTOR_UNAVAILABLE", "message": "Executor unavailable"}
    if status == "failed" and state not in {ExecutionState.REJECTED, ExecutionState.TIMED_OUT}:
        state = ExecutionState.FAILED
    duration = round((perf_counter() - start) * 1000, 3)
    # Audit commit is mandatory before any result leaves the service (fail closed).
    try:
        store.audit(execution_id=execution_id, tenant_id=tenant.tenant_id, script_id=str(script_id),
                    script_version=script["version"] if script else None, operation=operation,
                    status=status, duration_ms=duration, execution_mode=mode)
    except sqlite3.Error:
        state = ExecutionState.FAILED
        status, http_status, result = "failed", 503, None
        error = {"code": "AUDIT_UNAVAILABLE", "message": "Execution audit unavailable"}
    response = ExecutionResponse(execution_id=execution_id, execution_mode=mode, status=status, state=state, result=result,
                                 duration_ms=duration, error=error)
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))
