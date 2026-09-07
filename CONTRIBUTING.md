# Contributing

Thanks for considering it. This project only works if outside people can add
sources, and the repo is structured with that assumption.

## Ground rules

**1. Never commit a credential.** `.env`, `master_env`, and `*.key` are
gitignored. If you commit a secret by accident, tell a maintainer immediately —
rotating it is easy, and quietly force-pushing over it is not a fix, because it
stays in forks and caches.

**2. `ingest/licensing/` is security-relevant.** Read the next section before
touching it.

**3. Nothing privileged in `api/` or `web/`.** Those deploy to a public
serverless environment from a public repo. They get a read-only database key
constrained by row-level security, and nothing else. A PR that introduces the
service-role key into deployed code will be rejected on sight.

## Changing the licence allowlist

`ingest/licensing/licenses.yaml` decides what enters the catalog. A bug here
doesn't crash anything — it silently publishes something restricted as if it
were free, to consumers who are automated and cannot notice.

If you're adding a licence, your PR must answer:

> Does this licence impose any condition that an **automated, commercial,
> unattended** consumer could violate without realising?

- **Attribution** is machine-honourable. We pre-render the credit string at
  ingest so a caller can emit it without understanding licence semantics.
- **ShareAlike, NonCommercial, and NoDerivatives are not.** They impose
  obligations on the consumer's *finished work*, which we have no way to
  enforce or even observe. These stay rejected.

Include tests for both the admission *and* the rejection paths. `tests/` already
has the patterns.

## Adding a source

Sources are pluggable. Implement the interface in `ingest/sources/base.py` and
drop a module in `ingest/sources/`.

Your adapter's job is to **report what the source claims** — never to interpret
it. Pass the raw licence string, URL, or identifier straight to the gate and let
the gate decide. An adapter that "helpfully" maps an unknown licence onto a
known one has defeated the entire safety model.

A good source has **per-item, machine-readable** licence data. Openverse and
Wikimedia Commons do. Many archives do not, and adapters for those will see most
of their fetches correctly rejected — that's the system working, not a bug.

Also: send a descriptive `User-Agent` with contact info, and rate-limit
politely. These archives are mostly non-profits running on donations. Don't be
the reason they have to start blocking.

## Development

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r ingest/requirements.txt
pytest tests/ -q
```

The licence-gate tests need no credentials and no network. Start there.

## Pull requests

- One concern per PR.
- Tests for anything in `ingest/licensing/`, no exceptions.
- Match the surrounding style; there's no separate style guide.
- Explain *why*, not just *what* — the reasoning is the part that's hard to
  recover later.

## Reporting a licensing problem

If something in the catalog shouldn't be there, **please tell us** — open an
issue or use the report link in the UI. We'd much rather hear it from you than
from a lawyer, and a takedown is a single database update.
