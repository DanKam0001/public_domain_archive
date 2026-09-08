"""Tests for the fail-closed licence gate.

The tests that matter most here are the REJECTION tests. A bug that wrongly
rejects a good item costs us a row in the catalog. A bug that wrongly admits a
restricted item is the failure mode this entire project has to avoid, because
downstream consumers are automated and cannot sanity-check the result.

Several cases below use real data observed while sampling archive.org during
planning, not invented examples.
"""

import pytest

from ingest.licensing.gate import DEFAULT_ACCEPTED_TIERS, LicenseGate


@pytest.fixture
def gate() -> LicenseGate:
    return LicenseGate()


@pytest.fixture
def permissive_gate() -> LicenseGate:
    """A gate that also accepts attribution-tier licences."""
    return LicenseGate(accepted_tiers={"public_domain", "attribution"})


# ---------------------------------------------------------------------------
# The core rule: nothing gets in without positive identification.
# ---------------------------------------------------------------------------

def test_no_metadata_at_all_is_rejected(gate):
    """The archive.org case: 11 of 12 sampled items had no licence data at all.

    This is the single most important assertion in the suite. Silence must never
    read as permission.
    """
    d = gate.evaluate()
    assert d.allowed is False
    assert "no licence metadata" in d.reason


def test_empty_strings_are_rejected(gate):
    d = gate.evaluate(license_url="", license_text="", explicit_license_id="")
    assert d.allowed is False


def test_unrecognised_licence_is_rejected(gate):
    d = gate.evaluate(license_url="https://example.com/some-bespoke-licence")
    assert d.allowed is False
    assert "not on allowlist" in d.reason


def test_plausible_but_unknown_text_is_rejected(gate):
    """'Free to use' is not a licence. It must not pass."""
    d = gate.evaluate(license_text="Free to use, no restrictions!")
    assert d.allowed is False


# ---------------------------------------------------------------------------
# Restrictive licences.
# ---------------------------------------------------------------------------

def test_the_real_nasa_nc_item_is_rejected(gate):
    """Real item: nasahqphoto-6776102146, found inside archive.org's `nasa`
    collection, carrying CC BY-NC 2.0 while sitting next to genuine PD works.

    This is why collection membership can never imply a licence.
    """
    d = gate.evaluate(license_url="http://creativecommons.org/licenses/by-nc/2.0/")
    assert d.allowed is False
    assert "NonCommercial" in d.reason


@pytest.mark.parametrize(
    "url",
    [
        "https://creativecommons.org/licenses/by-nc/4.0/",
        "https://creativecommons.org/licenses/by-nd/4.0/",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        "https://creativecommons.org/licenses/by-nc-nd/4.0/",
        "http://rightsstatements.org/vocab/InC/1.0/",
        "http://rightsstatements.org/vocab/CNE/1.0/",
    ],
)
def test_restrictive_licences_are_rejected(gate, url):
    assert gate.evaluate(license_url=url).allowed is False


def test_sharealike_rejected_even_though_it_is_commercially_usable(gate):
    """CC BY-SA permits commercial use, so it is tempting to admit.

    We reject it because ShareAlike imposes copyleft on the CONSUMER's finished
    video — an obligation an automated pipeline would violate without ever
    knowing it had one.
    """
    d = gate.evaluate(license_url="https://creativecommons.org/licenses/by-sa/4.0/")
    assert d.allowed is False
    assert "ShareAlike" in d.reason


def test_conflicting_signals_reject(gate):
    """If one field says CC0 and another says NonCommercial, that is ambiguity.

    Ambiguity resolves to rejection, never to the permissive reading.
    """
    d = gate.evaluate(
        explicit_license_id="cc0",
        license_url="https://creativecommons.org/licenses/by-nc/4.0/",
    )
    assert d.allowed is False


def test_all_rights_reserved_text_rejected(gate):
    assert gate.evaluate(license_text="© 2019 Acme Corp. All Rights Reserved.").allowed is False


def test_fair_use_is_not_a_grant(gate):
    d = gate.evaluate(license_text="Reproduced under fair use")
    assert d.allowed is False
    assert "defence" in d.reason or "Fair use" in d.reason


# ---------------------------------------------------------------------------
# Admissions.
# ---------------------------------------------------------------------------

def test_cc0_url_admitted(gate):
    d = gate.evaluate(license_url="https://creativecommons.org/publicdomain/zero/1.0/")
    assert d.allowed is True
    assert d.license_id == "CC0-1.0"
    assert d.license.requires_attribution is False


def test_public_domain_mark_admitted(gate):
    d = gate.evaluate(license_url="https://creativecommons.org/publicdomain/mark/1.0/")
    assert d.allowed is True
    assert d.license_id == "PDM-1.0"


def test_openverse_style_bare_identifier_admitted(gate):
    """Openverse reports license='cc0', license_version='1.0' rather than a URL."""
    d = gate.evaluate(explicit_license_id="cc0")
    assert d.allowed is True
    assert d.license_id == "CC0-1.0"


def test_wikimedia_style_short_name_admitted(gate):
    """Wikimedia's extmetadata gives LicenseShortName: 'CC0'."""
    d = gate.evaluate(license_text="CC0")
    assert d.allowed is True
    assert d.license_id == "CC0-1.0"


# ---------------------------------------------------------------------------
# Tiers: attribution content is allowlisted but opt-in.
# ---------------------------------------------------------------------------

def test_cc_by_rejected_by_default_phase1_policy(gate):
    """CC-BY is a legitimate licence, but Phase 1 runs zero-obligation only."""
    assert DEFAULT_ACCEPTED_TIERS == frozenset({"public_domain"})
    d = gate.evaluate(license_url="https://creativecommons.org/licenses/by/4.0/")
    assert d.allowed is False
    assert "not accepted by this run" in d.reason
    # It was still correctly *identified* — this is a policy rejection, not a
    # failure to recognise the licence.
    assert d.license_id == "CC-BY-4.0"


def test_cc_by_admitted_when_tier_enabled(permissive_gate):
    d = permissive_gate.evaluate(license_url="https://creativecommons.org/licenses/by/4.0/")
    assert d.allowed is True
    assert d.license.requires_attribution is True


def test_attribution_string_rendered_for_cc_by(permissive_gate):
    d = permissive_gate.evaluate(license_url="https://creativecommons.org/licenses/by/4.0/")
    credit = permissive_gate.attribution_for(
        d.license, creator="Jane Doe", title="A Mountain",
        source_url="https://example.org/item/1",
    )
    assert "Jane Doe" in credit and "A Mountain" in credit
    assert "https://example.org/item/1" in credit


def test_no_attribution_string_for_public_domain(gate):
    """None, not empty string — a consumer must be able to tell 'no credit
    required' apart from 'credit unknown'."""
    d = gate.evaluate(explicit_license_id="cc0")
    assert gate.attribution_for(d.license, creator="X", title="Y",
                                source_url="https://example.org") is None


# ---------------------------------------------------------------------------
# Evidence retention.
# ---------------------------------------------------------------------------

def test_raw_claim_is_preserved_on_admission(gate):
    raw = {"license": "cc0", "fetched_at": "2026-09-07"}
    d = gate.evaluate(explicit_license_id="cc0", raw=raw)
    assert d.raw == raw


def test_raw_claim_is_preserved_on_rejection(gate):
    """We keep the evidence for rejected items too — it is how we can later
    answer 'why did you exclude this?'"""
    raw = {"license": "by-nc", "fetched_at": "2026-09-07"}
    d = gate.evaluate(license_url="https://creativecommons.org/licenses/by-nc/2.0/", raw=raw)
    assert d.allowed is False
    assert d.raw == raw


def test_gate_cannot_be_tricked_by_a_source_inventing_a_licence(gate):
    """A source adapter cannot widen the allowlist by asserting an id."""
    d = gate.evaluate(explicit_license_id="TOTALLY-FREE-1.0")
    assert d.allowed is False


# ---------------------------------------------------------------------------
# Regression: identifier matching must be exact, never fuzzy.
#
# Found by pointing the live Wikimedia harvester at Category:CC-BY-NC-4.0. A
# 1967 newspaper scan (File:Chicago_Seed_September_1967_issue.pdf) was admitted
# as PDM-1.0 on the strength of its structured field `License: pd`, because the
# old prefix matcher saw that "pdm10" starts with "pd".
#
# The item really was public domain — PD-US-no-notice — so the *verdict* was
# right. The mechanism was not: we would have written a licence identifier the
# source never claimed. These tests pin the distinction.
# ---------------------------------------------------------------------------

def test_bare_pd_is_not_the_public_domain_mark(gate):
    """The original bug. 'pd' is a generic status, PDM-1.0 is a specific
    instrument published by Creative Commons. Conflating them records a false
    claim even when the item is genuinely free."""
    d = gate.evaluate(explicit_license_id="pd")
    assert d.license_id != "PDM-1.0"


def test_bare_pd_maps_to_the_generic_public_domain_entry(gate):
    """Real case: Wikimedia's structured `License: pd`. Admitted — but recorded
    accurately, as a public domain assertion with no named instrument."""
    d = gate.evaluate(explicit_license_id="pd", license_text="Public domain")
    assert d.allowed is True
    assert d.license_id == "PD-GENERIC"
    assert d.license.requires_attribution is False


def test_identifier_prefixes_do_not_match(gate):
    """No prefix fallback in any direction.

    Note these are true prefixes, not punctuation variants: "cc0-" normalises to
    "cc0" and legitimately matches the CC0 alias, which is normalisation working
    as intended rather than the fuzzy matching this test guards against.
    """
    for value in ("c", "cc", "p", "pdm-1", "cc0-1"):
        assert gate.evaluate(explicit_license_id=value).license_id is None, value


def test_generic_public_domain_not_matchable_from_free_text(gate):
    """PD-GENERIC is admissible only from a structured licence field.

    The phrase appears constantly in ordinary prose — including in sentences
    saying something is *not* public domain, which must never admit an item.
    """
    assert gate.evaluate(license_text="This work is in the public domain").allowed is False
    assert gate.evaluate(license_text="This photo is not in the public domain").allowed is False


def test_openverse_bare_ids_still_work(gate):
    """The aliases the live sources actually send, pinned so a future edit to
    licenses.yaml that drops one fails loudly."""
    assert gate.evaluate(explicit_license_id="cc0").license_id == "CC0-1.0"
    assert gate.evaluate(explicit_license_id="pdm").license_id == "PDM-1.0"


# ---------------------------------------------------------------------------
# Legacy CC public domain dedication (pre-CC0, retired 2009).
#
# Found while assessing Phase 2 video supply: 12% of a Prelinger Archives
# sample carried this URL as their ONLY licence signal, and the gate rejected
# them for an unrecognised licence. The allowlist was missing a real dedication.
# ---------------------------------------------------------------------------

def test_legacy_cc_public_domain_dedication_admitted(gate):
    d = gate.evaluate(license_url="http://creativecommons.org/licenses/publicdomain/")
    assert d.allowed is True
    assert d.license_id == "CC-PDD"


def test_legacy_dedication_is_not_confused_with_cc0_or_pdm(gate):
    """A distinct instrument gets a distinct id — the catalog records what the
    source actually claimed, not the nearest modern equivalent."""
    d = gate.evaluate(license_url="http://creativecommons.org/licenses/publicdomain/")
    assert d.license_id not in {"CC0-1.0", "PDM-1.0"}


def test_legacy_dedication_url_does_not_admit_restrictive_neighbours(gate):
    """`creativecommons.org/licenses/...` is the prefix shared with every
    restrictive CC licence, so the new entry must not widen the door."""
    for url in ("http://creativecommons.org/licenses/by-nc/2.0/",
                "http://creativecommons.org/licenses/by-sa/4.0/",
                "http://creativecommons.org/licenses/by-nc-nd/3.0/"):
        assert gate.evaluate(license_url=url).allowed is False
