"""Fail-closed licence gate.

This module decides whether an item may enter the catalog. It is the single most
important piece of code in this project, and it is deliberately boring.

The rule, in one sentence: **an item is admitted only if it is positively
identified as carrying an allowlisted licence; everything else is rejected.**

Missing metadata is a rejection, not a maybe. Ambiguity is a rejection. An
unrecognised licence string is a rejection. There is no branch in this file that
admits an item because nothing said it was restricted.

Why it is written this way
--------------------------
The catalog's whole value proposition is that the caller does not have to verify
rights per item. Most callers are automated pipelines with no human reviewing
results, and they monetise. If we admit something restrictive and present it as
cleared, we are the party asserting a false claim — and "the consumer promised
not to monetise" is not available to us as a defence, because a script calling
``auto_broll()`` has no mechanism to make or honour such a promise.

Empirical basis: sampling archive.org's ``nasa`` collection (188,766 images)
found 11 of 12 items with *no licence metadata at all*, and the one item that did
carry a licence was ``CC BY-NC 2.0`` — NonCommercial — mirrored from Flickr and
sitting inside the collection most people assume is uniformly public domain.
Collection membership is therefore not evidence of licence. Only a per-item,
positively-matched licence is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_REGISTRY_PATH = Path(__file__).with_name("licenses.yaml")

# Tiers that a given ingest run is willing to accept. Phase 1 defaults to the
# strictest possible setting: zero-obligation content only. CC-BY is implemented
# and allowlisted, but is opt-in, because attribution is an obligation and we
# would rather prove the pipeline on content that carries none.
DEFAULT_ACCEPTED_TIERS = frozenset({"public_domain"})


class LicenseRegistryError(RuntimeError):
    """Raised when the allowlist itself is malformed.

    This is fatal on purpose. A gate that cannot load its allowlist must not
    fall back to permitting anything.
    """


@dataclass(frozen=True)
class License:
    id: str
    tier: str
    name: str
    url: str
    match_ids: tuple[str, ...] = ()
    match_urls: tuple[str, ...] = ()
    match_tokens: tuple[str, ...] = ()
    jurisdiction: str | None = None

    @property
    def requires_attribution(self) -> bool:
        return self.tier == "attribution"


@dataclass(frozen=True)
class Decision:
    """The gate's verdict. ``allowed`` is the only field callers should branch on."""

    allowed: bool
    reason: str
    license: License | None = None
    # The source's claim, verbatim and unedited. Stored on every item —
    # admitted or not — because if a licence later proves wrong, this is the
    # record of what we were told and when. It is the difference between a
    # good-faith error and negligence.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def license_id(self) -> str | None:
        return self.license.id if self.license else None


class LicenseGate:
    def __init__(
        self,
        accepted_tiers: frozenset[str] | set[str] = DEFAULT_ACCEPTED_TIERS,
        registry_path: Path | None = None,
    ) -> None:
        self._licenses, self._denied = _load_registry(registry_path or _REGISTRY_PATH)
        self._accepted_tiers = frozenset(accepted_tiers)

        unknown = self._accepted_tiers - {lic.tier for lic in self._licenses}
        if unknown:
            raise LicenseRegistryError(f"accepted_tiers names unknown tier(s): {sorted(unknown)}")

    # -- public API ---------------------------------------------------------

    def evaluate(
        self,
        *,
        license_url: str | None = None,
        license_text: str | None = None,
        explicit_license_id: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> Decision:
        """Decide whether one item may enter the catalog.

        Every parameter is optional and every parameter may be ``None`` — which
        is the common case in real data, and which must result in a rejection.

        ``explicit_license_id`` is for sources that state a canonical licence
        identifier themselves (Openverse does). It is still validated against the
        allowlist; a source cannot introduce a licence we have not vetted.
        """
        raw = dict(raw or {})

        # 1. Denylist first. An item matching a known-restrictive licence is
        #    rejected with a precise reason even if some other field on the same
        #    item happens to look permissive. Conflicting signals mean ambiguity,
        #    and ambiguity means no.
        haystack = " ".join(
            part.lower() for part in (license_url, license_text, explicit_license_id) if part
        )
        for pattern, reason in self._denied:
            if pattern in haystack:
                return Decision(False, f"denied: {reason}", None, raw)

        # 2. Positive identification. Note the ordering: an explicit canonical id
        #    is the strongest signal, then a URL match, then a free-text token.
        #    Free text is last because it is the weakest and most forgeable.
        lic = None
        if explicit_license_id:
            lic = self._by_id(explicit_license_id)
        if lic is None and license_url:
            lic = self._by_url(license_url)
        if lic is None and license_text:
            lic = self._by_text(license_text)

        # 3. Fail closed. This is the branch that catches the 11-of-12 archive.org
        #    case: no licence information at all.
        if lic is None:
            if not haystack.strip():
                return Decision(False, "rejected: no licence metadata present", None, raw)
            return Decision(False, "rejected: licence not on allowlist", None, raw)

        # 4. Allowlisted, but is this run willing to take on its obligations?
        if lic.tier not in self._accepted_tiers:
            return Decision(
                False,
                f"rejected: {lic.id} is tier '{lic.tier}', not accepted by this run",
                lic,
                raw,
            )

        return Decision(True, f"admitted: {lic.id}", lic, raw)

    def attribution_for(self, lic: License, *, creator: str | None, title: str | None,
                        source_url: str) -> str | None:
        """Pre-render the credit string at ingest time.

        Rendered once here rather than at query time so an automated consumer can
        emit a correct credit without parsing licence semantics it does not
        understand. Returns ``None`` for tiers that impose no obligation, so a
        caller can distinguish "no credit needed" from "credit unknown".
        """
        if not lic.requires_attribution:
            return None
        who = creator or "Unknown author"
        what = title or "Untitled"
        return f'"{what}" by {who}, licensed under {lic.name} — source: {source_url}'

    # -- internals ----------------------------------------------------------

    def _by_id(self, value: str) -> License | None:
        """Exact identifier match only — against the licence's own id or its
        declared aliases in ``match_ids``.

        There is deliberately no prefix or substring fallback. A previous
        version accepted the shortest unambiguous prefix, which silently mapped
        Wikimedia's generic ``pd`` ("public domain", by expiry/notice/statute)
        onto the specific ``PDM-1.0`` instrument, because "pdm10" happens to
        start with "pd". The item was genuinely public domain, so the verdict
        looked fine — but we would have recorded a licence the source never
        claimed. Sources that use a bare identifier must have it listed in
        ``match_ids`` explicitly, which makes the mapping reviewable in a diff
        rather than emergent from string arithmetic.
        """
        want = _normalise(value)
        if not want:
            return None
        for lic in self._licenses:
            if _normalise(lic.id) == want:
                return lic
            if any(_normalise(alias) == want for alias in lic.match_ids):
                return lic
        return None

    def _by_url(self, value: str) -> License | None:
        v = value.lower().rstrip("/")
        for lic in self._licenses:
            for frag in lic.match_urls:
                if frag.lower().rstrip("/") in v:
                    return lic
        return None

    def _by_text(self, value: str) -> License | None:
        v = f" {re.sub(r'[^a-z0-9. ]+', ' ', value.lower())} "
        v = re.sub(r"\s+", " ", v)
        for lic in self._licenses:
            for token in lic.match_tokens:
                if f" {token.lower()} " in v:
                    return lic
        return None


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _load_registry(path: Path) -> tuple[tuple[License, ...], tuple[tuple[str, str], ...]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - operational failure
        raise LicenseRegistryError(f"allowlist missing at {path}") from exc

    if not isinstance(data, dict) or "licenses" not in data:
        raise LicenseRegistryError(f"malformed allowlist at {path}")

    tiers = set(data.get("tiers") or {})
    licenses: list[License] = []
    for entry in data["licenses"]:
        tier = entry.get("tier")
        if tier not in tiers:
            raise LicenseRegistryError(f"licence {entry.get('id')!r} has unknown tier {tier!r}")
        licenses.append(
            License(
                id=entry["id"],
                tier=tier,
                name=entry["name"],
                url=entry["url"],
                match_ids=tuple(entry.get("match_ids") or ()),
                match_urls=tuple(entry.get("match_urls") or ()),
                match_tokens=tuple(entry.get("match_tokens") or ()),
                jurisdiction=entry.get("jurisdiction"),
            )
        )

    denied = tuple(
        (str(d["pattern"]).lower(), str(d["reason"])) for d in (data.get("denied") or [])
    )
    return tuple(licenses), denied
