"""Mock authentication only. Token mappings are supplied by trusted server setup."""

from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import Depends

TENANTS = {"demo-tenant-1": "Deutsche Bahn demo", "demo-tenant-2": "Lufthansa demo"}


@dataclass(frozen=True)
class Principal:
    tenant_id: str

    def __post_init__(self):
        if self.tenant_id not in TENANTS:
            raise ValueError("Unknown demo tenant")


bearer = HTTPBearer(auto_error=False)


def authenticated_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    principal = request.app.state.mock_tokens.get(credentials.credentials) if credentials else None
    if principal is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    return principal
