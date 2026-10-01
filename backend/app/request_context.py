"""Per-request context (request id, client IP) for audit records and logs.

Set by RequestContextMiddleware; read anywhere in the same request, including
BackgroundTasks, which run in a copy of the request's context. Scheduler jobs
run outside any request and see None.
"""
import re
import uuid
from contextvars import ContextVar
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied id only if it is short and boring, so it cannot
# smuggle log-injection payloads into audit rows.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[Optional[str]] = ContextVar("client_ip", default=None)


def current_request_id() -> Optional[str]:
    return request_id_var.get()


def current_client_ip() -> Optional[str]:
    return client_ip_var.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        rid = supplied if _SAFE_ID.match(supplied) else uuid.uuid4().hex
        rid_token = request_id_var.set(rid)
        ip_token = client_ip_var.set(request.client.host if request.client else None)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(rid_token)
            client_ip_var.reset(ip_token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
