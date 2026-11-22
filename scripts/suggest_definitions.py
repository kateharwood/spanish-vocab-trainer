"""Ask Claude for clearer definitions of every word in a study session, once.

    python scripts/suggest_definitions.py            # the most recent session
    python scripts/suggest_definitions.py SESSION_ID

The app already does this in the background for a session's new words; this
covers the rest (due reviews too). Words that already have a suggestion are
skipped, so running it again only fills gaps. Suggestions appear on the study
card the next time each word comes up.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.definitions import DefinitionHelper, anthropic_suggester  # noqa: E402
from app.store import get_store  # noqa: E402


def latest_session_id(store):
    if store.backend == "supabase":
        rows = (
            store.client.table("study_sessions").select("id")
            .order("started_at", desc=True).limit(1).execute().data
        )
        return rows[0]["id"] if rows else None
    with store._connect() as conn:
        row = conn.execute(
            "select id from study_sessions order by started_at desc limit 1"
        ).fetchone()
    return row["id"] if row else None


def main():
    store = get_store()
    suggest = anthropic_suggester()
    if suggest is None:
        raise SystemExit("Set ANTHROPIC_API_KEY in .env first.")

    session_id = sys.argv[1] if len(sys.argv) > 1 else latest_session_id(store)
    if not session_id:
        raise SystemExit("No study sessions yet.")

    families = []
    for row in store.list_session_queue(session_id):
        if row["source"] != "repeat" and row["ranks"] not in families:
            families.append(row["ranks"])

    executor = ThreadPoolExecutor(max_workers=5)
    helper = DefinitionHelper(store, suggest, executor=executor)
    queued = helper.queue_families(families)
    print("%d word families in the session, asking Claude about %d..." % (len(families), queued))
    executor.shutdown(wait=True)

    ranks = [rank for family in families for rank in family]
    words = {w["rank"]: w for w in store.list_words()}
    rows = sorted(store.get_suggestions(ranks), key=lambda row: row["rank"])
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] == "pending":
            print("  %-16s %s  ->  %s" % (
                words[row["rank"]]["spanish"], row["original_english"], row["suggested_english"]))
    print("Done:", ", ".join("%d %s" % (n, status) for status, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
