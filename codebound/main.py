from pathlib import Path
from collections.abc import Mapping

from fastapi import Depends, FastAPI, HTTPException, Query

from codebound.datasets import DEFAULT_DATA_DIR, DatasetUnavailableError, list_datasets, september_totals
from codebound.auth import Principal, authenticated_tenant
from codebound.execution import MockSandboxExecutor, SandboxExecutor
from codebound.scripts import router
from codebound.storage import ScriptStore
from codebound.sandbox import ExecutionGate, SandboxSettings
from codebound.docker_executor import LocalDockerExecutor


def create_app(data_dir: Path = DEFAULT_DATA_DIR, *, db_path: Path | None = None,
               mock_tokens: Mapping[str, Principal] | None = None,
               executor: SandboxExecutor | None = None,
               settings: SandboxSettings | None = None, connector_broker=None, ollama_client=None) -> FastAPI:
    settings = settings or SandboxSettings.from_env()
    if settings.mode == "local_docker" and executor is not None:
        raise ValueError("Trusted executor overrides are disabled in local_docker mode")
    app = FastAPI(title="Codebound - local script execution prototype", version="0.2.0")
    app.state.mock_tokens = dict(mock_tokens or {})
    app.state.store = ScriptStore(db_path or data_dir / "codebound.sqlite3")
    app.state.sandbox_settings = settings
    app.state.execution_gate = ExecutionGate(settings)
    app.state.executor = (LocalDockerExecutor(data_dir, settings, connector_broker) if settings.mode == "local_docker" else
                          executor if executor is not None else MockSandboxExecutor(data_dir))
    app.include_router(router)
    from codebound.llm.api import router as ai_router
    from codebound.llm.service import AIService
    app.state.ai = AIService(app.state.store, data_dir, connector_broker, ollama_client)
    app.include_router(ai_router)

    @app.get("/health")
    def health() -> dict:
        if settings.mode == "local_docker":
            from codebound.sandbox import RuntimeUnavailable
            try:
                app.state.executor.ready()
            except (RuntimeUnavailable, ValueError, KeyError):
                raise HTTPException(503, "Local Docker worker unavailable") from None
            return {"status": "ok", "sandbox_mode": settings.mode,
                    "isolated_runtime": "LOCAL_DOCKER_AVAILABLE"}
        return {"status": "ok", "sandbox_mode": settings.mode,
                "isolated_runtime": "NOT_IMPLEMENTED"}

    @app.get("/datasets")
    def datasets(tenant: Principal = Depends(authenticated_tenant)) -> dict:
        entries = list_datasets(data_dir, tenant.tenant_id)
        if connector_broker is not None and settings.mode == "local_docker":
            entries += connector_broker.datasets(tenant.tenant_id)
        return {"datasets": entries}

    @app.get("/datasets/{dataset_id}/aggregations/september")
    def september(dataset_id: str, year: int = Query(2026, ge=1, le=9998),
                  tenant: Principal = Depends(authenticated_tenant)) -> dict:
        try:
            return september_totals(data_dir, dataset_id, year, tenant_id=tenant.tenant_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="Dataset not found") from None
        except DatasetUnavailableError:
            raise HTTPException(status_code=503, detail="Dataset unavailable") from None

    return app


app = create_app()
