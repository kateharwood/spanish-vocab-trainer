-- Archived section, and the keep-or-archive choice for new words.
-- Safe to run more than once in the Supabase SQL editor.

-- Archived families keep their level but never come up in study.
alter table words add column if not exists archived boolean not null default false;

-- When you chose to keep studying or archive a word. Until then it comes up
-- with a halo and both options, in Sort and Study.
alter table words add column if not exists decided_at timestamptz;
