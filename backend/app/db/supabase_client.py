import logging
import threading

from supabase import Client, ClientOptions, create_client

from backend.app.config import settings

logger = logging.getLogger(__name__)

POSTGREST_TIMEOUT_SECONDS = 15

_client: Client | None = None
_lock = threading.Lock()


class SupabaseNotConfigured(RuntimeError):
    pass


def get_supabase_client() -> Client:
    """Process-wide Supabase client using the service role key.

    This bypasses RLS and must only be used server-side. Created lazily so that
    importing the app does not require credentials, and shared so every service
    reuses one HTTP connection pool (httpx clients are thread-safe).
    """
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
                raise SupabaseNotConfigured("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
            _client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY,
                options=ClientOptions(
                    postgrest_client_timeout=POSTGREST_TIMEOUT_SECONDS,
                    auto_refresh_token=False,
                    persist_session=False,
                ),
            )
    return _client
