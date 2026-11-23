-- Run this in the Supabase SQL editor before importing words.
-- "group" is a reserved word in SQL, so the column is named group_code.

create table if not exists words (
    id          bigint generated always as identity primary key,
    rank        integer not null unique,
    spanish     text    not null,
    english     text    not null,
    frequency   bigint,
    group_code  text,
    family_id   integer,
    alt_english text,
    archived    boolean not null default false,
    decided_at  timestamptz,
    updated_at  timestamptz not null default now()
);

-- Variants of one word (number/gender and related verb forms) share a family_id,
-- which is the rank of the family's main word (by default its most frequent
-- member). Null means the word stands alone.
alter table words add column if not exists family_id integer;

-- A secondary meaning shown smaller under the main definition.
alter table words add column if not exists alt_english text;

-- Archived families keep their level but never come up in study. decided_at
-- is when you chose to keep or archive a word; until then it comes up with a
-- halo and both options.
alter table words add column if not exists archived boolean not null default false;
alter table words add column if not exists decided_at timestamptz;

alter table words drop constraint if exists words_group_code_check;
alter table words add constraint words_group_code_check
    check (group_code is null or group_code in ('0a', '0b', 'n', '1', '2', '3', '4', 'v'));

create index if not exists words_group_code_idx on words (group_code);
create index if not exists words_family_id_idx on words (family_id);

create table if not exists study_cards (
    rank         integer primary key references words(rank) on delete cascade,
    card_data    jsonb not null,
    due          timestamptz not null,
    last_review  timestamptz,
    review_count integer not null default 0,
    updated_at   timestamptz not null default now()
);
create index if not exists study_cards_due_idx on study_cards (due);

create table if not exists study_sessions (
    id           text primary key,
    status       text not null check (status in ('active', 'completed', 'abandoned')),
    started_at   timestamptz not null default now(),
    completed_at timestamptz
);
create index if not exists study_sessions_status_idx on study_sessions (status);

create table if not exists study_session_queue (
    id           text primary key,
    session_id   text not null references study_sessions(id) on delete cascade,
    card_key     integer not null,
    ranks        jsonb not null,
    source       text not null check (source in ('due', 'new', 'repeat')),
    position     integer not null,
    available_at timestamptz not null,
    completed_at timestamptz
);
create index if not exists study_queue_session_idx
    on study_session_queue (session_id, completed_at, available_at, position);

create table if not exists review_logs (
    id          text primary key,
    session_id  text not null references study_sessions(id) on delete cascade,
    card_key    integer not null,
    ranks       jsonb not null,
    rating      integer not null check (rating between 1 and 4),
    source      text not null,
    reviewed_at timestamptz not null,
    next_due    timestamptz not null
);
create index if not exists review_logs_reviewed_idx on review_logs (reviewed_at);

create table if not exists definition_suggestions (
    rank              integer primary key references words(rank) on delete cascade,
    original_english  text not null,
    suggested_english text,
    original_alt      text,
    suggested_alt     text,
    note              text not null default '',
    status            text not null
        check (status in ('pending', 'accepted', 'dismissed', 'unchanged', 'failed')),
    updated_at        timestamptz not null default now()
);

-- The app connects with the secret key, which bypasses RLS.
-- Enabling RLS with no policies keeps the table closed to publishable/anon keys.
alter table words enable row level security;
alter table study_cards enable row level security;
alter table study_sessions enable row level security;
alter table study_session_queue enable row level security;
alter table review_logs enable row level security;
alter table definition_suggestions add column if not exists original_alt text;
alter table definition_suggestions add column if not exists suggested_alt text;
alter table definition_suggestions enable row level security;
