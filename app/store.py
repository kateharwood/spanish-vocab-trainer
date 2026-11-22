"""Word storage backed by Supabase, or a local SQLite file when Supabase is unconfigured.

Words are keyed by `rank`, which is unique in the source list and stable across
both backends.
"""

import os
import json
import sqlite3
import threading
from typing import Dict, Iterable, List, Optional

from dotenv import load_dotenv

load_dotenv()

GROUPS = ["0a", "1", "2", "3", "4", "n", "0b", "v"]

GROUP_LABELS = {
    "0a": "Level 0",
    "1": "Level 1",
    "2": "Level 2",
    "3": "Level 3",
    "4": "Level 4",
    "n": "Bad entries",
    "0b": "Don't care to learn",
    "v": "Verb practice",
}

# Groups that are never surfaced during study.
ARCHIVED_GROUPS = {"0a", "0b", "n"}


class SqliteStore:
    backend = "sqlite"

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                create table if not exists words (
                    rank       integer primary key,
                    spanish    text not null,
                    english    text not null,
                    frequency  integer,
                    group_code text,
                    family_id  integer
                )
                """
            )
            columns = {row["name"] for row in conn.execute("pragma table_info(words)")}
            if "family_id" not in columns:
                conn.execute("alter table words add column family_id integer")
            conn.executescript(
                """
                create table if not exists study_cards (
                    rank         integer primary key,
                    card_data    text not null,
                    due          text not null,
                    last_review  text,
                    review_count integer not null default 0,
                    updated_at   text not null
                );
                create index if not exists study_cards_due_idx on study_cards (due);

                create table if not exists study_sessions (
                    id           text primary key,
                    status       text not null,
                    started_at   text not null,
                    completed_at text
                );
                create index if not exists study_sessions_status_idx
                    on study_sessions (status);

                create table if not exists study_session_queue (
                    id           text primary key,
                    session_id   text not null,
                    card_key     integer not null,
                    ranks        text not null,
                    source       text not null,
                    position     integer not null,
                    available_at text not null,
                    completed_at text
                );
                create index if not exists study_queue_session_idx
                    on study_session_queue (session_id, completed_at, available_at, position);

                create table if not exists review_logs (
                    id          text primary key,
                    session_id  text not null,
                    card_key    integer not null,
                    ranks       text not null,
                    rating      integer not null,
                    source      text not null,
                    reviewed_at text not null,
                    next_due    text not null
                );
                create index if not exists review_logs_reviewed_idx
                    on review_logs (reviewed_at);

                create table if not exists definition_suggestions (
                    rank              integer primary key,
                    original_english  text not null,
                    suggested_english text,
                    note              text not null default '',
                    status            text not null,
                    updated_at        text not null,
                    original_alt      text,
                    suggested_alt     text
                );
                """
            )
            columns = {row["name"] for row in conn.execute("pragma table_info(words)")}
            if "alt_english" not in columns:
                conn.execute("alter table words add column alt_english text")
            columns = {
                row["name"]
                for row in conn.execute("pragma table_info(definition_suggestions)")
            }
            for column in ("original_alt", "suggested_alt"):
                if column not in columns:
                    conn.execute(
                        "alter table definition_suggestions add column %s text" % column
                    )

    def upsert_words(self, rows: Iterable[Dict]) -> int:
        rows = list(rows)
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                insert into words (rank, spanish, english, frequency, group_code)
                values (:rank, :spanish, :english, :frequency, :group_code)
                on conflict(rank) do update set
                    spanish = excluded.spanish,
                    english = excluded.english,
                    frequency = excluded.frequency,
                    group_code = coalesce(excluded.group_code, words.group_code)
                """,
                rows,
            )
        return len(rows)

    def list_words(self) -> List[Dict]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "select rank, spanish, english, alt_english, frequency, group_code, "
                "family_id from words order by rank"
            )
            return [dict(row) for row in cur.fetchall()]

    def set_family(self, ranks: List[int], family_id: Optional[int]) -> int:
        if not ranks:
            return 0
        placeholders = ",".join("?" for _ in ranks)
        with self._lock, self._connect() as conn:
            conn.execute(
                "update words set family_id = ? where rank in (%s)" % placeholders,
                [family_id] + ranks,
            )
        return len(ranks)

    def set_families(self, updates: Dict[int, Optional[int]]) -> int:
        """Restore per-rank family membership in one shot, used by undo."""
        with self._lock, self._connect() as conn:
            conn.executemany(
                "update words set family_id = ? where rank = ?",
                [(family, rank) for rank, family in updates.items()],
            )
        return len(updates)

    def assign(self, ranks: List[int], group_code: Optional[str]) -> int:
        if not ranks:
            return 0
        placeholders = ",".join("?" for _ in ranks)
        with self._lock, self._connect() as conn:
            conn.execute(
                "update words set group_code = ? where rank in (%s)" % placeholders,
                [group_code] + ranks,
            )
        return len(ranks)

    def assign_many(self, updates: Dict[int, Optional[str]]) -> int:
        """Restore differing groups in one shot, used by undo."""
        with self._lock, self._connect() as conn:
            conn.executemany(
                "update words set group_code = ? where rank = ?",
                [(group, rank) for rank, group in updates.items()],
            )
        return len(updates)

    def update_word(
        self, rank: int, spanish: str, english: str, alt_english: Optional[str] = None
    ) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "update words set spanish = ?, english = ?, alt_english = ? where rank = ?",
                (spanish, english, alt_english or None, rank),
            )
        return cur.rowcount

    def list_study_cards(self) -> List[Dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "select rank, card_data, due, last_review, review_count, updated_at "
                "from study_cards"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["card_data"] = json.loads(item["card_data"])
            result.append(item)
        return result

    def upsert_study_cards(self, rows: Iterable[Dict]) -> int:
        rows = list(rows)
        payload = [
            {
                **row,
                "card_data": json.dumps(row["card_data"]),
            }
            for row in rows
        ]
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                insert into study_cards
                    (rank, card_data, due, last_review, review_count, updated_at)
                values
                    (:rank, :card_data, :due, :last_review, :review_count, :updated_at)
                on conflict(rank) do update set
                    card_data = excluded.card_data,
                    due = excluded.due,
                    last_review = excluded.last_review,
                    review_count = excluded.review_count,
                    updated_at = excluded.updated_at
                """,
                payload,
            )
        return len(rows)

    def get_active_session(self) -> Optional[Dict]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "select id, status, started_at, completed_at from study_sessions "
                "where status = 'active' order by started_at desc limit 1"
            ).fetchone()
        return dict(row) if row else None

    def get_latest_session(self) -> Optional[Dict]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "select id, status, started_at, completed_at from study_sessions "
                "order by started_at desc limit 1"
            ).fetchone()
        return dict(row) if row else None

    def create_study_session(self, session: Dict, queue: Iterable[Dict]) -> int:
        queue = list(queue)
        with self._lock, self._connect() as conn:
            conn.execute(
                "insert into study_sessions (id, status, started_at, completed_at) "
                "values (:id, :status, :started_at, :completed_at)",
                session,
            )
            conn.executemany(
                """
                insert into study_session_queue
                    (id, session_id, card_key, ranks, source, position,
                     available_at, completed_at)
                values
                    (:id, :session_id, :card_key, :ranks, :source, :position,
                     :available_at, :completed_at)
                """,
                [{**row, "ranks": json.dumps(row["ranks"])} for row in queue],
            )
        return len(queue)

    def list_session_queue(self, session_id: str) -> List[Dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "select id, session_id, card_key, ranks, source, position, "
                "available_at, completed_at from study_session_queue "
                "where session_id = ? order by position, available_at",
                (session_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["ranks"] = json.loads(item["ranks"])
            result.append(item)
        return result

    def append_session_queue(self, row: Dict) -> int:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert into study_session_queue
                    (id, session_id, card_key, ranks, source, position,
                     available_at, completed_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], row["session_id"], row["card_key"],
                    json.dumps(row["ranks"]), row["source"], row["position"],
                    row["available_at"], row.get("completed_at"),
                ),
            )
        return 1

    def complete_queue_item(self, queue_id: str, completed_at: str) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "update study_session_queue set completed_at = ? where id = ?",
                (completed_at, queue_id),
            )
        return cur.rowcount

    def complete_pending_for_ranks(
        self, session_id: str, ranks: List[int], completed_at: str
    ) -> int:
        wanted = set(ranks)
        pending = [
            row["id"] for row in self.list_session_queue(session_id)
            if row["completed_at"] is None and wanted.intersection(row["ranks"])
        ]
        with self._lock, self._connect() as conn:
            conn.executemany(
                "update study_session_queue set completed_at = ? where id = ?",
                [(completed_at, queue_id) for queue_id in pending],
            )
        return len(pending)

    def set_session_status(
        self, session_id: str, status: str, completed_at: Optional[str]
    ) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "update study_sessions set status = ?, completed_at = ? where id = ?",
                (status, completed_at, session_id),
            )
        return cur.rowcount

    def insert_review_log(self, row: Dict) -> int:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert into review_logs
                    (id, session_id, card_key, ranks, rating, source,
                     reviewed_at, next_due)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], row["session_id"], row["card_key"],
                    json.dumps(row["ranks"]), row["rating"], row["source"],
                    row["reviewed_at"], row["next_due"],
                ),
            )
        return 1

    def get_suggestions(self, ranks: List[int]) -> List[Dict]:
        if not ranks:
            return []
        placeholders = ",".join("?" for _ in ranks)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "select rank, original_english, suggested_english, original_alt, "
                "suggested_alt, note, status, updated_at from definition_suggestions "
                "where rank in (%s)" % placeholders,
                list(ranks),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_suggestions(self, rows: Iterable[Dict]) -> int:
        rows = list(rows)
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                insert into definition_suggestions
                    (rank, original_english, suggested_english, original_alt,
                     suggested_alt, note, status, updated_at)
                values
                    (:rank, :original_english, :suggested_english, :original_alt,
                     :suggested_alt, :note, :status, :updated_at)
                on conflict(rank) do update set
                    original_english = excluded.original_english,
                    suggested_english = excluded.suggested_english,
                    original_alt = excluded.original_alt,
                    suggested_alt = excluded.suggested_alt,
                    note = excluded.note,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                [{"original_alt": None, "suggested_alt": None, **row} for row in rows],
            )
        return len(rows)

    def set_suggestion_status(self, rank: int, status: str, updated_at: str) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "update definition_suggestions set status = ?, updated_at = ? where rank = ?",
                (status, updated_at, rank),
            )
        return cur.rowcount


class SupabaseStore:
    backend = "supabase"

    def __init__(self, url: str, service_role_key: str):
        from supabase import create_client

        self.client = create_client(url, service_role_key)

    def upsert_words(self, rows: Iterable[Dict]) -> int:
        # An upsert overwrites every column it sends, so a re-import carrying no
        # group would erase sorting already done. Keep the stored group and
        # family whenever the incoming row does not supply one.
        stored = {word["rank"]: word for word in self.list_words()}
        payload = []
        for row in rows:
            row = dict(row)
            previous = stored.get(row["rank"], {})
            if not row.get("group_code"):
                row["group_code"] = previous.get("group_code")
            if not row.get("family_id"):
                row["family_id"] = previous.get("family_id")
            payload.append(row)

        for start in range(0, len(payload), 500):
            self.client.table("words").upsert(
                payload[start : start + 500], on_conflict="rank"
            ).execute()
        return len(payload)

    def list_words(self) -> List[Dict]:
        words: List[Dict] = []
        page_size = 1000
        start = 0
        while True:
            resp = (
                self.client.table("words")
                .select("rank, spanish, english, alt_english, frequency, group_code, family_id")
                .order("rank")
                .range(start, start + page_size - 1)
                .execute()
            )
            batch = resp.data or []
            words.extend(batch)
            if len(batch) < page_size:
                return words
            start += page_size

    def assign(self, ranks: List[int], group_code: Optional[str]) -> int:
        if not ranks:
            return 0
        for start in range(0, len(ranks), 200):
            chunk = ranks[start : start + 200]
            self.client.table("words").update({"group_code": group_code}).in_(
                "rank", chunk
            ).execute()
        return len(ranks)

    def assign_many(self, updates: Dict[int, Optional[str]]) -> int:
        by_group: Dict[Optional[str], List[int]] = {}
        for rank, group in updates.items():
            by_group.setdefault(group, []).append(rank)
        for group, ranks in by_group.items():
            self.assign(ranks, group)
        return len(updates)

    def set_family(self, ranks: List[int], family_id: Optional[int]) -> int:
        if not ranks:
            return 0
        for start in range(0, len(ranks), 200):
            chunk = ranks[start : start + 200]
            self.client.table("words").update({"family_id": family_id}).in_(
                "rank", chunk
            ).execute()
        return len(ranks)

    def set_families(self, updates: Dict[int, Optional[int]]) -> int:
        by_family: Dict[Optional[int], List[int]] = {}
        for rank, family in updates.items():
            by_family.setdefault(family, []).append(rank)
        for family, ranks in by_family.items():
            self.set_family(ranks, family)
        return len(updates)

    def update_word(
        self, rank: int, spanish: str, english: str, alt_english: Optional[str] = None
    ) -> int:
        self.client.table("words").update(
            {"spanish": spanish, "english": english, "alt_english": alt_english or None}
        ).eq("rank", rank).execute()
        return 1

    def list_study_cards(self) -> List[Dict]:
        rows: List[Dict] = []
        start = 0
        while True:
            batch = (
                self.client.table("study_cards")
                .select("rank, card_data, due, last_review, review_count, updated_at")
                .range(start, start + 999)
                .execute()
                .data
                or []
            )
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
            start += 1000

    def upsert_study_cards(self, rows: Iterable[Dict]) -> int:
        rows = list(rows)
        for start in range(0, len(rows), 200):
            self.client.table("study_cards").upsert(
                rows[start : start + 200], on_conflict="rank"
            ).execute()
        return len(rows)

    def get_active_session(self) -> Optional[Dict]:
        data = (
            self.client.table("study_sessions")
            .select("id, status, started_at, completed_at")
            .eq("status", "active")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        return data[0] if data else None

    def get_latest_session(self) -> Optional[Dict]:
        data = (
            self.client.table("study_sessions")
            .select("id, status, started_at, completed_at")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        return data[0] if data else None

    def create_study_session(self, session: Dict, queue: Iterable[Dict]) -> int:
        queue = list(queue)
        self.client.table("study_sessions").insert(session).execute()
        for start in range(0, len(queue), 200):
            self.client.table("study_session_queue").insert(
                queue[start : start + 200]
            ).execute()
        return len(queue)

    def list_session_queue(self, session_id: str) -> List[Dict]:
        rows: List[Dict] = []
        start = 0
        while True:
            batch = (
                self.client.table("study_session_queue")
                .select(
                    "id, session_id, card_key, ranks, source, position, "
                    "available_at, completed_at"
                )
                .eq("session_id", session_id)
                .order("position")
                .range(start, start + 999)
                .execute()
                .data
                or []
            )
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
            start += 1000

    def append_session_queue(self, row: Dict) -> int:
        self.client.table("study_session_queue").insert(row).execute()
        return 1

    def complete_queue_item(self, queue_id: str, completed_at: str) -> int:
        self.client.table("study_session_queue").update(
            {"completed_at": completed_at}
        ).eq("id", queue_id).execute()
        return 1

    def complete_pending_for_ranks(
        self, session_id: str, ranks: List[int], completed_at: str
    ) -> int:
        wanted = set(ranks)
        pending = [
            row["id"] for row in self.list_session_queue(session_id)
            if row["completed_at"] is None and wanted.intersection(row["ranks"])
        ]
        if pending:
            self.client.table("study_session_queue").update(
                {"completed_at": completed_at}
            ).in_("id", pending).execute()
        return len(pending)

    def set_session_status(
        self, session_id: str, status: str, completed_at: Optional[str]
    ) -> int:
        self.client.table("study_sessions").update(
            {"status": status, "completed_at": completed_at}
        ).eq("id", session_id).execute()
        return 1

    def insert_review_log(self, row: Dict) -> int:
        self.client.table("review_logs").insert(row).execute()
        return 1

    def get_suggestions(self, ranks: List[int]) -> List[Dict]:
        rows: List[Dict] = []
        ranks = list(ranks)
        for start in range(0, len(ranks), 200):
            rows.extend(
                self.client.table("definition_suggestions")
                .select(
                    "rank, original_english, suggested_english, original_alt, "
                    "suggested_alt, note, status, updated_at"
                )
                .in_("rank", ranks[start : start + 200])
                .execute()
                .data
                or []
            )
        return rows

    def upsert_suggestions(self, rows: Iterable[Dict]) -> int:
        rows = list(rows)
        if rows:
            self.client.table("definition_suggestions").upsert(
                rows, on_conflict="rank"
            ).execute()
        return len(rows)

    def set_suggestion_status(self, rank: int, status: str, updated_at: str) -> int:
        self.client.table("definition_suggestions").update(
            {"status": status, "updated_at": updated_at}
        ).eq("rank", rank).execute()
        return 1


def get_store():
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (
        os.environ.get("SUPABASE_SECRET_KEY", "")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    ).strip()
    if url and key:
        return SupabaseStore(url, key)
    return SqliteStore(os.environ.get("SQLITE_PATH", "vocab.db"))
