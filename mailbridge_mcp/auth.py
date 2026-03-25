from __future__ import annotations

import hmac
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Paths that bypass Bearer auth: health check + OAuth discovery probes from MCP clients.
# Claude.ai's remote MCP client probes /.well-known/ before sending Bearer tokens;
# returning 401 on those paths triggers an OAuth flow loop. Let them 404 naturally.
_AUTH_EXEMPT_PREFIXES = ("/health", "/.well-known/")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, api_key: str) -> None:
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(
            auth[7:], self.api_key
        ):
            # WWW-Authenticate header tells MCP clients to use Bearer auth
            # instead of falling back to OAuth discovery
            return JSONResponse(
                {"error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
