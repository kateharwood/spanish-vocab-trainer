-- Store Claude's suggested English definitions for new words.
-- Safe to run more than once in the Supabase SQL editor.

create table if not exists definition_suggestions (
    rank              integer primary key references words(rank) on delete cascade,
    original_english  text not null,
    suggested_english text,
    note              text not null default '',
    status            text not null
        check (status in ('pending', 'accepted', 'dismissed', 'unchanged', 'failed')),
    updated_at        timestamptz not null default now()
);

alter table definition_suggestions enable row level security;
