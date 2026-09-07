# Licensing policy

This is the project's legal spine. It is a policy enforced in code
(`ingest/licensing/`), not a disclaimer in a footer.

*Not legal advice. Written by maintainers who are not lawyers, erring
deliberately toward caution.*

---

## 1. The rule

**An item enters the catalog only if it is positively identified as carrying an
allowlisted licence. Everything else is excluded.**

There is no branch anywhere in the pipeline that admits an item because nothing
said it was restricted. Silence is a rejection.

| Signal | Outcome |
|---|---|
| Matched allowlisted licence | admitted |
| No licence metadata | rejected |
| Unrecognised licence | rejected |
| NC / ND / SA / in-copyright / "copyright not evaluated" | rejected |
| Conflicting signals | rejected |

### Why so strict

Our consumers are disproportionately **automated and commercial**. A script
calling `auto_broll()` has no human reviewing what comes back, and cannot make
or honour a promise about how the result gets used. "The user agreed not to
monetise" is therefore unavailable to us as a safety argument — there is no user
in the loop to agree.

The catalog's entire value is that a caller doesn't have to verify rights per
item. That value only exists if the verification genuinely happened upstream. A
catalog that is *usually* right is worse than useless here, because it invites
exactly the trust it can't honour.

### The evidence behind the rule

Sampling archive.org's `nasa` collection (188,766 images) during planning:

- **11 of 12** items sampled had **no licence metadata of any kind** — no
  `licenseurl`, no `rights`, no `possible-copyright-status`.
- Across 50 items: `licenseurl` on **0/50**, `rights` on **5/50**.
- The one item carrying a licence had **CC BY-NC 2.0** — NonCommercial — a
  mirrored Flickr stream inside the collection most people assume is uniformly
  public domain.

**Conclusion: collection membership is not evidence of licence.** Any design
that trusts "this collection is public domain" is trusting something we have
measured to be false.

## 2. What's allowed, and why

### Admitted — `public_domain` tier

| Licence | Note |
|---|---|
| **CC0 1.0** | Explicit dedication to the public domain |
| **Public Domain Mark 1.0** | Asserts no known copyright |
| **PD-GENERIC** | A source's *structured* assertion of public domain status without naming a specific instrument — e.g. Wikimedia's `License: pd`, covering works PD by expiry, by lack of copyright notice, or by statute. Admissible only from a structured licence field, **never** from free text, because "public domain" appears constantly in prose including in sentences denying it |
| **US federal works** (17 U.S.C. §105) | Only where the source asserts federal authorship per item. **Not** valid for contractor or third-party material an agency merely republished — a common and dangerous confusion |

We record the licence the source actually claimed, never a near-neighbour. A
generic public domain assertion is stored as `PD-GENERIC`, not silently upgraded
to `PDM-1.0` — the two are different claims, and the catalog should not invent
precision the source did not provide. Identifier matching is exact for the same
reason: an early version of the gate mapped `pd` onto `PDM-1.0` by prefix, and
though that item turned out to be genuinely public domain, the record would have
been false. See `tests/test_license_gate.py` for the pinned regression.

### Allowlisted but opt-in — `attribution` tier

**CC BY** (2.0 / 3.0 / 4.0). Free for commercial use and derivatives, but
requires a credit line. Attribution is *machine-honourable*: we pre-render the
credit at ingest so a consumer can emit it without parsing licence semantics.

Phase 1 runs `public_domain` only — zero-obligation content — because it's
better to prove the pipeline on content that carries no conditions at all.

### Rejected

| Licence | Why |
|---|---|
| **NonCommercial (NC)** | Our consumers monetise. Serving this hands them a violation. |
| **NoDerivatives (ND)** | Media that can't be cut or cropped is useless as b-roll. |
| **ShareAlike (SA)** | Permits commercial use — but imposes copyleft on the consumer's *finished video*. An automated pipeline would violate this without ever knowing it had an obligation. This is the subtlest of the three and the most tempting to admit. |
| **In Copyright** | Self-explanatory. |
| **Copyright Not Evaluated** | An explicit statement that nobody checked. |
| **"Fair use"** | A defence, not a grant, and not transferable to our consumers. |

## 3. What we assert, and what we don't

**We are an index pointing at a claim. We are not a warrantor of that claim.**

This distinction is legally meaningful and costs nothing to get right, so we get
it right everywhere:

- ✅ "Source states: CC0 — verify at [link]"
- ❌ "This image is public domain"

Concretely:

1. **Every item links back to its source.** Non-negotiable. It's how a user
   verifies independently, and it's the strongest good-faith argument we have.
2. **The source's original claim is stored verbatim** (`raw_license_meta`),
   permanently, for admitted *and* rejected items. If a licence later proves
   wrong, this is the record of what we were told and when — the difference
   between a good-faith error and negligence.
3. **We never rewrite or "clean up" a claim.** Normalisation happens in a
   separate field; the original is immutable.

## 4. Jurisdiction

**Public domain is not global.** A work can be public domain in the US and still
in copyright in the EU.

- US: works published before 1930, and US federal government works.
- EU and much of the world: generally life of the author + 70 years.

**Our determinations are US-based.** Consumers outside the US should verify
locally. We state this plainly rather than burying it, because a user in Germany
acting on a US public domain determination is a real and foreseeable failure
mode.

## 5. Takedown

Being reachable and responsive is most of what good faith looks like in
practice.

- A "report a licensing problem" link is visible in the UI and the API docs.
- Reports land in the `takedown_requests` table — a tracked record, not an email
  someone forgets.
- `items.takedown` is a soft delete enforced in the row-level security policy: a
  takedown is one `UPDATE`, effective immediately across the API and site, and
  reversible if the claim turns out to be wrong.
- We do not require a formal DMCA notice before acting. If a claim is plausible,
  we remove first and discuss after.

## 6. Known limitations

Stated openly because pretending otherwise is how this goes wrong:

- **We trust the source's claim.** If Wikimedia says CC0 and Wikimedia is wrong,
  we're wrong too. Mitigation is provenance (prefer sources with real review
  processes), evidence retention, and a fast takedown path — not omniscience.
- **Third-party content inside public domain collections** is the hardest case:
  a federal agency republishing a contractor's copyrighted photo. Per-item
  licence checking catches this only when the source labels it.
- **Freedom of panorama, trademark, personality rights, and model releases are
  out of scope.** A public domain photograph of a person or a building may still
  carry non-copyright restrictions on some uses. We check *copyright*, and say so.
