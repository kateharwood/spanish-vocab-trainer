-- For running on Vercel (or any host with more than one server instance).
-- Safe to run more than once in the Supabase SQL editor.

-- "working" marks a definition request Claude is still answering, so every
-- server instance can show "Claude is looking…" and none asks twice.
alter table definition_suggestions drop constraint if exists definition_suggestions_status_check;
alter table definition_suggestions add constraint definition_suggestions_status_check
    check (status in ('pending', 'accepted', 'dismissed', 'unchanged', 'failed', 'working'));
