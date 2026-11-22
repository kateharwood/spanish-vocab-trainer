"""Move existing review due dates to the start of their calendar day.

Reviews used to come due at the exact minute they were last rated; they now
come due at midnight (server time zone) of the day they are scheduled for.
This updates cards scheduled before that change. Safe to run more than once.

    python scripts/snap_due_dates.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fsrs import State  # noqa: E402

from app.store import get_store  # noqa: E402
from app.study import iso, parse_time, start_of_local_day, utcnow  # noqa: E402


def main():
    store = get_store()
    now = utcnow()
    changed = []
    for row in store.list_study_cards():
        if row["card_data"].get("state") != State.Review.value:
            continue
        due = parse_time(row["due"])
        snapped = start_of_local_day(due)
        if snapped == due:
            continue
        changed.append(
            {
                **row,
                "card_data": {**row["card_data"], "due": iso(snapped)},
                "due": iso(snapped),
                "updated_at": iso(now),
            }
        )
    if changed:
        store.upsert_study_cards(changed)
    print("Moved %d review due dates to the start of their day." % len(changed))


if __name__ == "__main__":
    main()
