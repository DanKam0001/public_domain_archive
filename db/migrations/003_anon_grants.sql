-- 003_anon_grants.sql — let the deployed API read as `anon`, and nothing more.
--
-- The security model is that the public surface holds no privileged credential.
-- A direct Postgres connection would defeat this: it authenticates as the
-- `postgres` role, which BYPASSES row level security entirely, so the takedown
-- filter and the api_keys protection would exist only by convention.
--
-- Instead the API calls PostgREST with the anon key. Every request is then
-- executed as the `anon` role and RLS actually applies. As a bonus this removes
-- serverless connection pooling from the picture — it is plain HTTP.
--
-- RLS and GRANTs are separate gates and both must pass. RLS says *which rows*;
-- GRANT says *whether the role may touch the table at all*.

-- Read published items. The policy from 001 restricts this to takedown = false.
grant usage on schema public to anon;
grant select on public.items to anon;

-- Report a licensing problem. Insert only: anyone may file a report, nobody may
-- read the queue.
grant insert on public.takedown_requests to anon;

-- Search. SECURITY INVOKER (the default) means the function body still runs as
-- anon, so RLS on items applies inside it too — the takedown filter cannot be
-- bypassed by calling the function.
grant execute on function public.search_items(vector, integer, text, real, integer) to anon;

-- Explicitly NOT granted, listed so the omissions are visible and deliberate:
--   * api_keys        — key verification happens server-side against a hash,
--                       never by selecting this table
--   * insert/update/delete on items — ingestion writes with the service role
--                       from a local machine, never from the deployed API
--   * select on takedown_requests   — reports are write-only to the public

revoke all on public.api_keys from anon;
