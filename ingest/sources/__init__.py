"""Source registry.

Adding a source means adding a module here and one line to ``SOURCES``. The
registry is keyed by the adapter's ``name``, which is also what lands in the
``items.source`` column — so renaming a key orphans existing rows.
"""

from .base import RawItem, Source
from .openverse import OpenverseSource
from .wikimedia import WikimediaSource

SOURCES: dict[str, type[Source]] = {
    OpenverseSource.name: OpenverseSource,
    WikimediaSource.name: WikimediaSource,
}

__all__ = ["RawItem", "Source", "SOURCES", "OpenverseSource", "WikimediaSource"]
