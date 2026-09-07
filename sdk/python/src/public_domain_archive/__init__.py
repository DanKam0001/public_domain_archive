"""Python client for the Public Domain Archive.

    from public_domain_archive import Client

    archive = Client()
    items = archive.search("snow covered mountains", limit=10)
    archive.download(items[0], "./footage")

No API key is needed to search or download. One only raises rate limits.

Every item in the catalog has been licence-verified individually at ingest —
anything missing, ambiguous, or restrictive is excluded rather than admitted
with a warning. Each item still carries `source_url` so you can verify the
claim yourself, and `license.statement` reports what the source said rather
than asserting it as fact.
"""

from .client import Client, DEFAULT_BASE_URL
from .errors import ArchiveError, NotFound, RateLimited, RequestError, ServerError
from .models import Item, License

__version__ = "0.1.0"

__all__ = [
    "Client", "Item", "License", "DEFAULT_BASE_URL",
    "ArchiveError", "NotFound", "RateLimited", "RequestError", "ServerError",
]
