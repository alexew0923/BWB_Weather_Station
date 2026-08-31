"""Data access boundary.

Everything that knows about HTTP, Google Sheets URL shapes or ``.env`` files
lives here and nowhere else. The analysis stages below this layer operate on a
canonical dataset and cannot tell where it came from, which is what makes them
testable without a network and reusable against a future database, a file drop
or an external weather reference.
"""

from .sheets import (
    SourceReference,
    fetch_csv_text,
    normalize_sheet_url,
    read_local_csv_text,
    resolve_historical_source,
    resolve_live_source,
    retrieve_csv_text,
)

__all__ = [
    "SourceReference",
    "fetch_csv_text",
    "normalize_sheet_url",
    "read_local_csv_text",
    "resolve_historical_source",
    "resolve_live_source",
    "retrieve_csv_text",
]
