from typing import Any, Callable, List

# Supabase/PostgREST caps an unranged select at `max-rows` (1000 by default) and
# returns the truncated set without error. Any select whose result feeds a loop
# over the whole table, or a sum, must page explicitly.
PAGE_SIZE = 1000
MAX_ROWS = 100_000
TOTAL_COUNT_HEADER = "X-Total-Count"


def set_total(response: Any, result: Any) -> None:
    """Expose the unpaged row count of a `select(..., count="exact")` so
    clients can render page numbers; the body stays a plain list."""
    count = getattr(result, "count", None)
    if count is not None:
        response.headers[TOTAL_COUNT_HEADER] = str(count)


def fetch_all(build_query: Callable[[], Any], page_size: int = PAGE_SIZE) -> List[dict]:
    """Run a supabase-py select page by page.

    `build_query` must return a fresh, ordered query builder on each call
    (builders are mutable, so they cannot be reused across pages).
    """
    rows: List[dict] = []
    offset = 0
    while offset < MAX_ROWS:
        res = build_query().range(offset, offset + page_size - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows
