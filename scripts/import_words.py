"""Load the frequency spreadsheet into whichever backend is configured.

    python scripts/import_words.py Spanish_Top_1000_Words.xlsx

With --from-sqlite it instead copies the local SQLite rows (groups included) into
Supabase, so sorting done before Supabase existed is not lost.
"""

import argparse
import os
import sys

from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.store import SqliteStore, get_store  # noqa: E402

EXPECTED_HEADERS = {"rank", "spanish", "english", "frequency"}


def read_xlsx(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)

    header = [str(cell).strip().lower() if cell else "" for cell in next(rows)]
    missing = EXPECTED_HEADERS - set(header)
    if missing:
        raise SystemExit("Spreadsheet is missing columns: %s" % ", ".join(sorted(missing)))
    index = {name: header.index(name) for name in EXPECTED_HEADERS}

    words = []
    for row in rows:
        if row is None or row[index["rank"]] is None:
            continue
        words.append(
            {
                "rank": int(row[index["rank"]]),
                "spanish": str(row[index["spanish"]]).strip(),
                "english": str(row[index["english"]]).strip(),
                "frequency": int(row[index["frequency"]]) if row[index["frequency"]] else None,
                "group_code": None,
            }
        )
    return words


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default="Spanish_Top_1000_Words.xlsx")
    parser.add_argument(
        "--from-sqlite",
        action="store_true",
        help="Copy the local SQLite rows (with their groups) into the configured backend.",
    )
    args = parser.parse_args()

    store = get_store()

    if args.from_sqlite:
        local = SqliteStore(os.environ.get("SQLITE_PATH", "vocab.db"))
        words = local.list_words()
        if store.backend == "sqlite":
            raise SystemExit("Supabase is not configured, so there is nothing to copy into.")
    else:
        words = read_xlsx(args.source)

    before = {w["rank"]: w.get("group_code") for w in store.list_words()}
    count = store.upsert_words(words)
    after = store.list_words()

    new_ranks = sum(1 for w in words if w["rank"] not in before)
    kept = sum(1 for w in after if w.get("group_code"))
    print(
        "Imported %d words into %s: %d new, %d total, %d still grouped."
        % (count, store.backend, new_ranks, len(after), kept)
    )


if __name__ == "__main__":
    main()
