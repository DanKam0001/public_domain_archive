"""SDK tests.

Offline by default — these exercise parsing, attribution handling, and error
mapping without touching the network, so they run in CI and on a laptop with no
connection. The live integration check lives in the API tests.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

pytest.importorskip("public_domain_archive")

from public_domain_archive import Client, Item, NotFound, RequestError  # noqa: E402

CC0_ROW = {
    "id": "6c25aad6-84ef-4a2a-bea1-5fa672e5f0fa",
    "title": "Snow at Joshua Tree",
    "creator": "Joshua Tree National Park",
    "source": "openverse",
    "source_url": "https://www.flickr.com/photos/115357548@N08/12489476433",
    "license": {
        "id": "PDM-1.0", "url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "tier": "public_domain", "requires_attribution": False,
        "statement": "Source states: PDM-1.0. Verify at https://example.org",
    },
    "attribution": None,
    "width": 1024, "height": 768,
    "similarity": 0.291,
}

CC_BY_ROW = {
    **CC0_ROW,
    "id": "11111111-2222-3333-4444-555555555555",
    "title": "A Mountain",
    "license": {
        "id": "CC-BY-4.0", "url": "https://creativecommons.org/licenses/by/4.0/",
        "tier": "attribution", "requires_attribution": True,
        "statement": "Source states: CC-BY-4.0. Verify at https://example.org",
    },
    "attribution": '"A Mountain" by Jane Doe, licensed under CC-BY-4.0 — source: https://example.org',
}


def test_parses_a_result_row():
    item = Item.from_json(CC0_ROW)
    assert item.title == "Snow at Joshua Tree"
    assert item.license.id == "PDM-1.0"
    assert item.similarity == pytest.approx(0.291)
    # The raw payload is retained so a caller can reach fields the SDK does not
    # model yet, rather than being blocked by our dataclass lagging the API.
    assert item.raw is CC0_ROW or item.raw == CC0_ROW


def test_public_domain_item_reports_no_credit_required():
    """None, not empty string — the distinction is load-bearing for an
    automated consumer deciding whether to render a credit."""
    item = Item.from_json(CC0_ROW)
    assert item.requires_attribution is False
    assert item.credit() is None


def test_attribution_item_carries_its_credit_line():
    item = Item.from_json(CC_BY_ROW)
    assert item.requires_attribution is True
    assert "Jane Doe" in item.credit()


def test_licence_statement_is_a_report_not_an_assertion():
    """The wording matters legally: we index a claim, we do not warrant it."""
    item = Item.from_json(CC0_ROW)
    assert item.license.statement.startswith("Source states:")


def test_credits_file_written_even_when_nothing_requires_credit(tmp_path):
    """A file saying 'nothing required' records that the question was asked.
    A missing file could mean that, or could mean the step was skipped."""
    path = Client.write_credits([Item.from_json(CC0_ROW)], tmp_path / "CREDITS.txt")
    text = path.read_text(encoding="utf-8")
    assert "No attribution is required" in text
    # Verification links appear regardless of obligation.
    assert CC0_ROW["source_url"] in text


def test_credits_file_highlights_items_needing_credit(tmp_path):
    items = [Item.from_json(CC0_ROW), Item.from_json(CC_BY_ROW)]
    text = Client.write_credits(items, tmp_path / "CREDITS.txt").read_text(encoding="utf-8")
    assert "1 of 2 items require" in text
    assert "CREDIT  :" in text
    assert "Jane Doe" in text


def test_every_item_gets_a_source_link_in_credits(tmp_path):
    items = [Item.from_json(CC0_ROW), Item.from_json(CC_BY_ROW)]
    text = Client.write_credits(items, tmp_path / "CREDITS.txt").read_text(encoding="utf-8")
    assert text.count("source  :") == 2


def test_empty_query_rejected_before_any_request():
    """Fails locally rather than spending a round trip to be told 400."""
    with pytest.raises(RequestError):
        Client().search("   ")


def test_http_404_becomes_not_found(monkeypatch):
    def raise_404(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", raise_404)
    with pytest.raises(NotFound):
        Client().get("6c25aad6-84ef-4a2a-bea1-5fa672e5f0fa")


def test_base_url_env_override(monkeypatch):
    monkeypatch.setenv("PDA_BASE_URL", "https://staging.example.org/")
    assert Client().base_url == "https://staging.example.org"


def test_search_sends_tier_filter(monkeypatch):
    """tier=public_domain is how a monetising pipeline asks for
    zero-obligation content only."""
    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"results": [CC0_ROW]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    items = Client().search("a red flower", tier="public_domain", limit=5)
    assert captured["body"]["tier"] == "public_domain"
    assert captured["body"]["limit"] == 5
    assert len(items) == 1
