"""The archive client.

    from public_domain_archive import Client

    archive = Client()
    for item in archive.search("snow covered mountains", limit=10):
        print(item.title, item.license.id)

    paths = archive.search_and_download("a steam locomotive", "./footage", limit=5)

Design notes
------------
Most callers are scripts running unattended, which drives three choices that
would look over-cautious in an interactive library:

* **Generous default timeout.** A cold container loads the CLIP text tower and
  the first search after a quiet spell can take ~15s. A 10s default would make
  the archive look broken to anyone who tried it once.
* **Automatic retry on 429 and 5xx**, with backoff, honouring ``Retry-After``.
  An unattended job should ride out a transient blip rather than fail a batch.
* **Attribution travels with the item**, and :meth:`write_credits` discharges it
  in one call. A script cannot notice a credit it forgot to render, so the
  library makes doing it right the path of least effort.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .errors import ArchiveError, NotFound, RateLimited, RequestError, ServerError
from .models import Item

DEFAULT_BASE_URL = "https://public-domain-archive.vercel.app"
USER_AGENT = "public-domain-archive-sdk/0.1 (+https://github.com/DanKam0001/public_domain_archive)"

#: Long on purpose — see module docstring. Cold starts are real and a caller
#: hitting one on their first ever call should get results, not a timeout.
DEFAULT_TIMEOUT = 60.0


class Client:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
    ) -> None:
        self.base_url = (base_url or os.environ.get("PDA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        # Browsing and downloading need no key. One only raises rate limits for
        # programmatic use.
        self.api_key = api_key or os.environ.get("PDA_API_KEY")
        self.timeout = timeout
        self.max_retries = max_retries

    # -- search -------------------------------------------------------------

    def search(self, query: str, *, limit: int = 24, tier: str | None = None) -> list[Item]:
        """Search by natural-language description.

        ``tier`` filters by licence obligation: ``"public_domain"`` returns only
        items with no conditions on reuse, which is what you want when the
        output is monetised and nobody will be checking credits.
        """
        if not query or not query.strip():
            raise RequestError("query must not be empty")

        payload: dict[str, Any] = {"query": query.strip(), "limit": limit}
        if tier:
            payload["tier"] = tier

        data = self._request("POST", "/api/search", body=payload)
        return [Item.from_json(row) for row in data.get("results", [])]

    def get(self, item_id: str) -> Item:
        """Full metadata for one item, including the source's original claim."""
        return Item.from_json(self._request("GET", f"/api/item/{item_id}"))

    def similar(self, item: Item | str, *, limit: int = 12) -> list[Item]:
        """Visually similar items — 'more like this one'."""
        item_id = item.id if isinstance(item, Item) else item
        data = self._request("GET", f"/api/item/{item_id}/similar?limit={limit}")
        return [Item.from_json(row) for row in data.get("results", [])]

    # -- download -----------------------------------------------------------

    def download(self, item: Item | str, dest: str | Path, *, overwrite: bool = False) -> Path:
        """Download one item's file.

        ``dest`` may be a directory (a filename is derived from the id) or a
        full path. Returns the written path.
        """
        item_id = item.id if isinstance(item, Item) else item
        dest = Path(dest)
        if dest.is_dir() or not dest.suffix:
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / f"{item_id}.jpg"
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() and not overwrite:
            return dest

        url = f"{self.base_url}/api/item/{item_id}/download"
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                # Written to a temporary name and moved into place, so an
                # interrupted download never leaves a truncated file that a
                # later run would mistake for complete.
                temporary = dest.with_suffix(dest.suffix + ".part")
                with open(temporary, "wb") as handle:
                    while chunk := response.read(65536):
                        handle.write(chunk)
                temporary.replace(dest)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise NotFound(f"item {item_id} not found") from error
            raise ArchiveError(f"download failed ({error.code})") from error
        return dest

    def search_and_download(
        self, query: str, dest: str | Path, *, limit: int = 10, tier: str | None = None,
    ) -> list[tuple[Item, Path]]:
        """Search, download every result, and write a credits file.

        The shape Phase 3's ``auto_broll(script)`` will build on: one call from
        a description to files on disk that are safe to use.
        """
        items = self.search(query, limit=limit, tier=tier)
        dest = Path(dest)
        pairs = [(item, self.download(item, dest)) for item in items]
        self.write_credits([item for item, _ in pairs], dest / "CREDITS.txt")
        return pairs

    # -- attribution --------------------------------------------------------

    @staticmethod
    def write_credits(items: Iterable[Item], path: str | Path) -> Path:
        """Write a credits file for a batch.

        Always written, even when nothing requires attribution — a file saying
        "no attribution required" is a record that the question was asked and
        answered, which is more useful to someone auditing a pipeline later than
        a missing file that could mean either thing.
        """
        items = list(items)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = ["Credits", "=" * 40, ""]
        requiring = [item for item in items if item.requires_attribution]

        if not requiring:
            lines.append(
                "No attribution is required for any item below — all are public "
                "domain or CC0. Verification links are included regardless."
            )
        else:
            lines.append(
                f"{len(requiring)} of {len(items)} items require the credit line "
                f"shown. Reproduce it wherever the work appears."
            )
        lines.append("")

        for item in items:
            lines.append(f"- {item.title or '(untitled)'}")
            lines.append(f"  licence : {item.license.id}")
            if item.attribution:
                lines.append(f"  CREDIT  : {item.attribution}")
            lines.append(f"  source  : {item.source_url}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    # -- internals ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = None
        headers = self._headers()
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    raise NotFound(f"not found: {path}") from error
                if error.code == 400:
                    raise RequestError(_message(error)) from error
                if error.code == 429 or error.code >= 500:
                    last_error = error
                    if attempt == self.max_retries - 1:
                        break
                    wait = float(error.headers.get("Retry-After") or 2 ** attempt)
                    time.sleep(min(wait, 30))
                    continue
                raise ArchiveError(f"request failed ({error.code}): {_message(error)}") from error
            except urllib.error.URLError as error:
                last_error = error
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2 ** attempt)

        if isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429:
            raise RateLimited("rate limited; retries exhausted") from last_error
        raise ServerError(f"request failed after {self.max_retries} attempts: {last_error}")


def _message(error: urllib.error.HTTPError) -> str:
    try:
        return json.loads(error.read()).get("error", str(error))
    except Exception:
        return str(error)
