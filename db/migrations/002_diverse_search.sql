-- 002_diverse_search.sql — stop near-duplicates flooding a results page
--
-- The problem, measured on the Phase 1 corpus: 292 of 1,038 rows (28%) are
-- redundant. The query "close up of texture" filled its entire first page with
-- five copies of one textile. Ranking quality is irrelevant if the grid shows
-- the same thing five times.
--
-- Why de-duplication needs TWO axes, not one
-- ------------------------------------------
-- Measured cosine distances on the real corpus:
--
--   same-title pairs      : median 0.154, only 12% below 0.05
--   different-title pairs : only 0.02% below 0.05
--
-- So the obvious fix — drop items whose embeddings are nearly identical —
-- catches genuine visual duplicates safely (almost no false positives) but
-- misses most of the problem. Ten photographs of ten *different* "Bellini"
-- carpets are visually distinct and would all survive, while still filling a
-- page with one apparent thing.
--
-- Conversely, collapsing purely on title would discard legitimately separate
-- content: the Shahnama folios share a title but are different paintings.
--
-- Hence both, with different jobs:
--   * `diversity`      removes visual near-duplicates (same scan uploaded twice)
--   * `max_per_title`  caps how much of a page any one titled work may occupy
--
-- This is deliberately done at QUERY time rather than at ingest. The duplicates
-- are often legitimately distinct items, so discarding them on the way in would
-- destroy real content we cannot get back; suppressing them per-query is
-- reversible and tunable.

-- Drop the 001 signature explicitly. `create or replace function` only replaces
-- a function with the SAME argument list — a different one creates an OVERLOAD,
-- and then any call using the defaults is ambiguous:
--   "function search_items(vector, integer) is not unique"
drop function if exists search_items(vector, integer, text);

create or replace function search_items(
    query_embedding vector(512),
    match_limit     integer default 24,
    filter_tier     text    default null,
    -- Minimum cosine distance between any two returned items. 0.06 sits well
    -- above the different-title p1 (0.12 is p1, so this is conservative) and
    -- below the same-title median, targeting true visual duplicates only.
    diversity       real    default 0.06,
    -- Maximum results sharing one title. 2 keeps a legitimate variant visible
    -- without letting one work own the page.
    max_per_title   integer default 2
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
language plpgsql
stable
as $$
declare
    candidate   record;
    kept        vector(512)[] := '{}';
    kept_titles text[]        := '{}';
    other       vector(512);
    suppressed  boolean;
    title_key   text;
    title_count integer;
begin
    -- Over-fetch, because we expect to discard a substantial fraction. 8x
    -- comfortably covers the measured 28% redundancy plus clustering.
    for candidate in
        select i.id, i.source, i.source_url, i.title, i.creator,
               i.license_id, i.license_tier, i.attribution,
               i.r2_key, i.thumb_key, i.width, i.height, i.embedding,
               1 - (i.embedding <=> query_embedding) as sim
        from items i
        where i.takedown = false
          and i.embedding is not null
          and (filter_tier is null or i.license_tier = filter_tier)
        order by i.embedding <=> query_embedding
        limit least(match_limit, 100) * 8
    loop
        suppressed := false;

        -- Axis 1: visual near-duplicate.
        foreach other in array kept loop
            if (candidate.embedding <=> other) < diversity then
                suppressed := true;
                exit;
            end if;
        end loop;

        -- Axis 2: too much of one titled work already on the page.
        if not suppressed and candidate.title is not null then
            title_key := lower(btrim(candidate.title));
            select count(*) into title_count
            from unnest(kept_titles) t where t = title_key;
            if title_count >= max_per_title then
                suppressed := true;
            end if;
        end if;

        if not suppressed then
            kept := kept || candidate.embedding;
            if candidate.title is not null then
                kept_titles := kept_titles || lower(btrim(candidate.title));
            end if;

            id           := candidate.id;
            source       := candidate.source;
            source_url   := candidate.source_url;
            title        := candidate.title;
            creator      := candidate.creator;
            license_id   := candidate.license_id;
            license_tier := candidate.license_tier;
            attribution  := candidate.attribution;
            r2_key       := candidate.r2_key;
            thumb_key    := candidate.thumb_key;
            width        := candidate.width;
            height       := candidate.height;
            similarity   := candidate.sim;
            return next;

            exit when coalesce(array_length(kept, 1), 0) >= match_limit;
        end if;
    end loop;
end;
$$;
