-- Add spaced-repetition scheduling and resumable study sessions.
-- Safe to run more than once in the Supabase SQL editor.

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

alter table study_cards enable row level security;
alter table study_sessions enable row level security;
alter table study_session_queue enable row level security;
alter table review_logs enable row level security;
