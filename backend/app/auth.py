"""Supabase JWT verification and role checks.

Two signing modes are supported, selected by the token's `alg` header:
  * HS256 — legacy Supabase projects; verified with SUPABASE_JWT_SECRET.
  * RS256/ES256 — asymmetric signing keys; verified against the project JWKS
    (SUPABASE_JWKS_URL, default `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`).

Verification always fails closed: an unconfigured key source is a 503, never a
pass. `exp` and `aud` are mandatory.
"""
import logging
import threading
import time

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from backend.app.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer()

ASYMMETRIC_ALGS = {"RS256", "ES256"}
OPERATOR_ROLES = {"operator", "admin"}
_JWKS_TTL_SECONDS = 600

_jwks_lock = threading.Lock()
_jwks_cache: dict = {"keys": None, "fetched_at": 0.0}


def _unauthorized(detail: str = "Invalid authentication credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _not_configured() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication is not configured",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _jwks_url() -> str:
    if settings.SUPABASE_JWKS_URL:
        return settings.SUPABASE_JWKS_URL
    if settings.SUPABASE_URL:
        return settings.SUPABASE_URL.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    return ""


def _get_jwks(force: bool = False) -> list:
    url = _jwks_url()
    if not url:
        raise _not_configured()
    with _jwks_lock:
        fresh = time.monotonic() - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS
        if _jwks_cache["keys"] is not None and fresh and not force:
            return _jwks_cache["keys"]
        try:
            resp = httpx.get(url, timeout=5.0)
            resp.raise_for_status()
            keys = resp.json().get("keys", [])
        except (httpx.HTTPError, ValueError) as e:
            logger.error("Failed to fetch JWKS: %s", e)
            if _jwks_cache["keys"] is not None:
                return _jwks_cache["keys"]
            raise _not_configured()
        _jwks_cache["keys"] = keys
        _jwks_cache["fetched_at"] = time.monotonic()
        return keys


def _find_jwk(kid: str | None) -> dict | None:
    for refresh in (False, True):
        for key in _get_jwks(force=refresh):
            if kid is None or key.get("kid") == kid:
                return key
    return None


def _decode(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        raise _unauthorized()

    alg = header.get("alg")
    options = {"require_exp": True, "require_aud": True, "verify_aud": True}
    try:
        if alg == "HS256":
            if not settings.SUPABASE_JWT_SECRET:
                raise _not_configured()
            return jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience=settings.JWT_AUDIENCE,
                options=options,
            )
        if alg in ASYMMETRIC_ALGS:
            key = _find_jwk(header.get("kid"))
            if key is None:
                raise _unauthorized()
            return jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=settings.JWT_AUDIENCE,
                options=options,
            )
    except JWTError:
        raise _unauthorized()
    # "none" and any other algorithm are rejected outright.
    raise _unauthorized()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    return _decode(credentials.credentials)


def get_current_user(payload: dict = Depends(verify_token)) -> dict:
    """Extracts the user from the verified JWT payload."""
    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized()
    app_metadata = payload.get("app_metadata") or {}
    return {
        "id": user_id,
        "email": payload.get("email"),
        "role": payload.get("role"),
        "app_role": app_metadata.get("cloudsentry_role"),
    }


def is_operator(user: dict) -> bool:
    """Operators may change cloud state or safety config.

    Granted by `app_metadata.cloudsentry_role` (settable only with the Supabase
    service role) or by an explicit OPERATOR_USER_IDS allowlist. Plain sign-up
    never grants it.
    """
    if user.get("app_role") in OPERATOR_ROLES:
        return True
    return user.get("id") in settings.operator_user_id_set()


def require_operator(user: dict = Depends(get_current_user)) -> dict:
    if not is_operator(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")
    return user


def actor_label(user: dict) -> str:
    return f"USER:{user['id']}"
