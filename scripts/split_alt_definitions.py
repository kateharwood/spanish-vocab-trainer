"""Split multi-sense definitions into a main definition and an alt definition.

Definitions like "came; wine" or "balls; (vulgar) testicles" become a main
definition ("came") and an alt definition ("wine"), shown smaller under it.
Only the first ";" outside parentheses splits, so "(a propósito) by the way"
stays whole. Words that already have an alt definition are left alone, so it's
safe to run more than once.

    python scripts/split_alt_definitions.py            # show what would change
    python scripts/split_alt_definitions.py --apply    # write it
"""

import argparse
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.definitions import split_definition  # noqa: E402
from app.store import get_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write the changes.")
    args = parser.parse_args()

    store = get_store()
    changes = []
    for word in store.list_words():
        if word.get("alt_english"):
            continue
        split = split_definition(word["english"])
        if split:
            changes.append((word, split))

    for word, (main_def, alt) in changes:
        print("  %-14s %s  ->  %s  |  alt: %s" % (word["spanish"], word["english"], main_def, alt))
    if not args.apply:
        print("\n%d definitions would be split. DRY RUN - pass --apply to write" % len(changes))
        return
    for word, (main_def, alt) in changes:
        store.update_word(word["rank"], word["spanish"], main_def, alt)
    print("\nSplit %d definitions." % len(changes))


if __name__ == "__main__":
    main()
