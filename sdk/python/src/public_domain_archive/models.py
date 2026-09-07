"""Typed results.

These mirror the API responses, with one addition that is not cosmetic:
:attr:`Item.requires_attribution` and :meth:`Item.credit` make the licence
obligation a first-class property of the object rather than something a caller
has to remember to look up.

The audience for this SDK is largely automated — scripts assembling video with
no human reviewing the output. Such a caller cannot notice a credit it was
supposed to render. So the obligation travels attached to the item, and
:func:`~public_domain_archive.client.Client.write_credits` makes discharging it
a single call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class License:
    id: str
    url: str | None
    tier: str
    requires_attribution: bool
    #: The source's claim as reported by us, phrased as a report and not an
    #: assertion — "Source states: CC0-1.0. Verify at <url>". Render this rather
    #: than composing your own wording.
    statement: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> License:
        return cls(
            id=data["id"],
            url=data.get("url"),
            tier=data.get("tier", "public_domain"),
            requires_attribution=bool(data.get("requires_attribution")),
            statement=data.get("statement", ""),
        )


@dataclass(frozen=True)
class Item:
    id: str
    title: str | None
    creator: str | None
    source: str
    #: The original listing. Always populated — it is how a licence claim is
    #: verified independently, so it is never omitted.
    source_url: str
    license: License
    #: Pre-rendered credit line, or None when the licence imposes none.
    #: None and "" mean different things: None is "no credit required",
    #: whereas an empty string would be "credit unknown".
    attribution: str | None
    width: int | None = None
    height: int | None = None
    thumbnail_url: str | None = None
    image_url: str | None = None
    similarity: float | None = None
    description: str | None = None
    mime: str | None = None
    bytes: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Item:
        return cls(
            id=data["id"],
            title=data.get("title"),
            creator=data.get("creator"),
            source=data.get("source", ""),
            source_url=data.get("source_url", ""),
            license=License.from_json(data.get("license", {"id": "UNKNOWN"})),
            attribution=data.get("attribution"),
            width=data.get("width"),
            height=data.get("height"),
            thumbnail_url=data.get("thumbnail_url"),
            image_url=data.get("image_url"),
            similarity=data.get("similarity"),
            description=data.get("description"),
            mime=data.get("mime"),
            bytes=data.get("bytes"),
            raw=data,
        )

    @property
    def requires_attribution(self) -> bool:
        return self.license.requires_attribution

    def credit(self) -> str | None:
        """The credit line to reproduce, or None if none is required."""
        return self.attribution

    def __str__(self) -> str:
        return f"{self.title or '(untitled)'} [{self.license.id}]"
