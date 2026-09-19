from supabase import create_client, Client
from backend.app.config import settings
import logging

logger = logging.getLogger(__name__)

def get_supabase_client() -> Client:
    """
    Returns a Supabase client configured with the service role key.
    This bypasses RLS and should only be used for backend operations.
    """
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        logger.warning("Supabase credentials not configured.")
        # We can still return a dummy or fail, but let's let create_client raise an error
        # if they are truly empty in a real scenario. 
        # For now, it might raise a validation error if URL is empty.
    
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY
    )

supabase: Client = get_supabase_client()
