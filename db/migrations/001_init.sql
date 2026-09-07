-- 001_init.sql — initial schema for public_domain_archive
--
-- Design notes worth reading before changing anything here:
--
--  * `raw_license_meta` stores the source's licence claim VERBATIM and forever.
--    If a licence later proves wrong, this is the evidence of what we were told
--    and when. Never edit or normalise it in place.
--  * `embed_model` is stored PER ROW, not in global config. If the embedding
--    model ever changes, mismatched rows become a detectable, migratable state
--    instead of silent corruption that returns confident nonsense.
--  * `fts` exists from day one even though Phase 1 ships vector search only.
--    It is the hedge against CLIP underperforming on archival material — see
--    docs/architecture.md. Having it here means hybrid retrieval is a query
--    change, not a migration.
--  * `takedown` is a soft delete enforced in the RLS policy, so a takedown is a
--    single UPDATE, effective immediately, and reversible if disputed.

create extension if not exists vector;
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- items
-- ---------------------------------------------------------------------------
create table if not exists items (
    id                uuid primary key default gen_random_uuid(),

    -- provenance: (source, source_id) is the natural key and makes re-ingestion
    -- idempotent, so a re-run updates rather than duplicating.
    source            text        not null,
    source_id         text        not null,
    source_url        text        not null,

    title             text,
    description       text,
    creator           text,

    -- licensing. license_id is always a canonical id from the ingest gate's
    -- allowlist — never a free-text string copied from the source.
    license_id        text        not null,
    license_url       text,
    license_tier      text        not null check (license_tier in ('public_domain','attribution')),
    attribution       text,        -- pre-rendered credit line; null when none required
    raw_license_meta  jsonb       not null default '{}'::jsonb,

    -- storage
    r2_key            text        not null,
    thumb_key         text,
    width             integer,
    height            integer,
    mime              text,
    bytes             bigint,

    -- retrieval
    embedding         vector(512),
    embed_model       text        not null,
    fts               tsvector generated always as (
                          to_tsvector(
                              'english',
                              coalesce(title, '') || ' ' || coalesce(description, '')
                          )
                      ) stored,

    ingested_at       timestamptz not null default now(),
    takedown          boolean     not null default false,
    takedown_reason   text,

    constraint items_source_unique unique (source, source_id)
);

-- Vector index. HNSW gives better recall/latency than IVFFlat and needs no
-- training step, which matters because we build it before the table is full.
create index if not exists items_embedding_idx
    on items using hnsw (embedding vector_cosine_ops);

create index if not exists items_fts_idx     on items using gin (fts);
create index if not exists items_license_idx on items (license_id);
create index if not exists items_source_idx  on items (source);

-- ---------------------------------------------------------------------------
-- api_keys — for the programmatic API only. Browsing and downloading via the
-- website never require a key; that is a product decision, not an oversight.
-- Only a hash is stored, so a database leak does not yield usable keys.
-- ---------------------------------------------------------------------------
create table if not exists api_keys (
    id           uuid primary key default gen_random_uuid(),
    key_hash     text        not null unique,
    label        text,
    created_at   timestamptz not null default now(),
    last_used_at timestamptz,
    revoked      boolean     not null default false,
    rate_limit   integer     not null default 60   -- requests/minute
);

-- ---------------------------------------------------------------------------
-- takedown_requests — a licensing dispute is a first-class record, not an
-- email someone forgets. Being reachable and responsive is most of what good
-- faith looks like in practice.
-- ---------------------------------------------------------------------------
create table if not exists takedown_requests (
    id           uuid primary key default gen_random_uuid(),
    item_id      uuid references items(id) on delete set null,
    reporter     text,
    claim        text        not null,
    created_at   timestamptz not null default now(),
    resolved_at  timestamptz,
    resolution   text
);

-- ---------------------------------------------------------------------------
-- Row level security.
--
-- The deployed API uses the ANON key only. The service-role key never leaves
-- the ingestion machine, so a credential leaked from the public repo or a bad
-- PR can at worst read data that is already public by definition.
-- ---------------------------------------------------------------------------
alter table items             enable row level security;
alter table api_keys          enable row level security;
alter table takedown_requests enable row level security;

drop policy if exists items_public_read on items;
create policy items_public_read
    on items for select
    using (takedown = false);

-- api_keys: no anon policy at all, so anon cannot read it. Key verification
-- happens in the API layer against a hash, not by selecting this table.

drop policy if exists takedowns_public_insert on takedown_requests;
create policy takedowns_public_insert
    on takedown_requests for insert
    with check (true);   -- anyone may report a problem; nobody may read the queue

-- ---------------------------------------------------------------------------
-- search_items — vector similarity as a callable function so the API sends a
-- query vector rather than composing SQL, and so the takedown filter can never
-- be forgotten by a caller.
-- ---------------------------------------------------------------------------
create or replace function search_items(
    query_embedding vector(512),
    match_limit     integer default 24,
    filter_tier     text    default null
)
returns table (
    id           uuid,
    source       text,
    source_url   text,
    title        text,
    creator      text,
    license_id   text,
    license_tier text,
    attribution  text,
    r2_key       text,
    thumb_key    text,
    width        integer,
    height       integer,
    similarity   float
)
language sql
stable
as $$
    select
        i.id, i.source, i.source_url, i.title, i.creator,
        i.license_id, i.license_tier, i.attribution,
        i.r2_key, i.thumb_key, i.width, i.height,
        1 - (i.embedding <=> query_embedding) as similarity
    from items i
    where i.takedown = false
      and i.embedding is not null
      and (filter_tier is null or i.license_tier = filter_tier)
    order by i.embedding <=> query_embedding
    limit least(match_limit, 100);
$$;
