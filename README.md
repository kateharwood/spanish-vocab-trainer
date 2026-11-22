# Spanish vocab sorter

The first 1000 Spanish words by frequency, plus a UI for sorting them into study
groups. Sorting is a one-time pass, but any word can be moved between groups later.

## Groups

| Code | Meaning | Study behavior |
| --- | --- | --- |
| `0a` | Level 0 | Archived |
| `1` | Level 1 | Shows up sometimes |
| `2` | Level 2 | Shows up the most |
| `3` | Level 3 | Shows up the most |
| `4` | Level 4 | Shows up the most, a few at a time |
| `n` | Bad entries | Archived |
| `0b` | Don't care to learn | Archived |
| `v` | Verb practice: know it, want to drill it | Shows up sometimes |

Words start with no group until they are sorted.

## Word families

Variants of the same word are studied together. `negro`, `negra`, and `negros`
are one entry; so are `hablar`, `hablando`, `hablado`, `hablarle`, and
`hablarte`. They appear on a single card, take up a single row, and share a
group, while each keeps its own rank and frequency on display.

Families are stored as a `family_id` on each word, which is the rank of its most
frequent member, so a merged entry stays where you would expect to find it in the
list. Groups still live on the individual words, so assigning a family writes the
group to every variant.

`scripts/build_families.py` proposes number/gender families only when English
glosses agree, which keeps `casa`/`caso` and `dios`/`día` apart. It also joins
infinitives with attached-pronoun forms, gerunds, and non-lexicalized past
participles. Participles used as separate nouns or adjectives (`pasado`,
`cuidado`, `estado`) stay independent. Shortened forms like `buen` and
accent-only pairs like `cuanto`/`cuánto` also stay separate.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

The app runs against a local SQLite file until Supabase is configured, so you can
start sorting right away.

```bash
.venv/bin/python scripts/import_words.py Spanish_Top_1000_Words.xlsx
.venv/bin/uvicorn app.main:app --port 8000 --reload
```

Open http://127.0.0.1:8000.

## Connecting Supabase

1. Run `db/schema.sql` in the Supabase SQL editor.
2. Put the project URL and the secret key (`sb_secret_...`, from Settings → API
   Keys) into `.env`. The publishable key will not work: the table has row level
   security enabled with no public policies, so only a server-side key can write.
3. Copy any sorting already done locally, then restart the server:

```bash
.venv/bin/python scripts/import_words.py --from-sqlite
```

For an existing database created before study sessions were added, run
`db/study_migration.sql` in the Supabase SQL editor.

## Study sessions

Study is the default mode. There is one session per calendar day: each contains
every active card currently due plus 10 unseen word families, and once it's
finished (or ended early) the next one opens at midnight. Reviews scheduled days
out also come due at midnight of their day, so studying earlier than yesterday
still includes them. New cards are chosen randomly with these long-term
weights: Level 3 40%, Level 4 40%, Level 2 10%, Level 1 5%, and Verb practice
5%. If one level has no unseen cards, its chance is redistributed among the
levels that do.

The front of a card shows every English meaning in the family. Reveal the
Spanish, then rate the recall:

- `1` Again
- `2` Hard
- `3` Good
- `4` Easy

FSRS schedules the next review from that rating. Good on a new word sends it
straight to review a few days out. Again and Hard on a word you're still
learning mark it **repeat**: it goes to the back of the session and comes back
after the cards still ahead of it, with no waiting at the end. An unfinished session resumes after a
refresh or restart.

Each revealed Spanish/English variant has an Edit button. The group picker moves
the entire family without erasing its review history. Moving it to Level 0, Bad
entries, or Don't care to learn removes its remaining cards from the session; so
does archiving or unsorting it from the List.

## Clearer definitions from Claude

The English glosses in the source spreadsheets are often terse or ambiguous.
When a study session starts, the app quietly asks Claude (`claude-opus-5-5`) to
review each word family the session introduces for the first time, and to
suggest a clearer definition: more context, the senses you'll actually meet, or
just cleaner formatting. Each word is asked about once.

The same happens while sorting: Sort asks about the card you're on and the next
few, so a suggestion is usually ready by the time you reveal the English.

After you reveal a card that has a suggestion, it appears with **Use this**
(replaces the English) and **Keep mine**. Nothing changes until you accept. If
Claude thinks the current definition is fine, no suggestion is shown. A word is
only ever asked about once: accept or dismiss it while sorting and it won't come
up again while studying.

To turn it on, add an API key to `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

With Supabase, also run `db/definitions_migration.sql` in the SQL editor once.
Without a key the feature stays off.

## Sorting UI

Sort and List remain available from the header. Assignments save immediately in
both.

### Cards

One unsorted word at a time in rank order, Spanish first. The translation stays
hidden until you reveal it, so your honest reaction decides the group.

| Key | Result |
| --- | --- |
| `space` (or click the card) | Reveal the English |
| `0` | Assign to Level 0, then advance |
| `1`–`4` | Assign to Levels 1–4, then advance |
| `d` | Assign to Don't care to learn, then advance |
| `b` | Assign to Bad entries, then advance |
| `v` | Assign to `v`, then advance |
| `u` | Clear the group, then advance |
| `→` / `←` | Skip forward, go back |
| `⌘Z` | Undo and return to that card |

### List

Adjacent words usually belong in the same group, so selection is built around
grabbing runs of rows.

| Action | Result |
| --- | --- |
| Drag across rows | Select a run |
| `shift` + click | Extend the run to that row |
| `⌘` / `ctrl` + click | Add or remove one row |
| `⌘A` | Select everything currently visible |
| `↑` `↓`, `shift` + arrows | Move the cursor, extend the selection |
| `0` | Assign the selection to Level 0 |
| `1`–`4` | Assign the selection to Levels 1–4 |
| `d` | Assign the selection to Don't care to learn |
| `b` | Assign the selection to Bad entries |
| `v` | Assign the selection to `v` |
| `u` | Clear the group (back to unsorted) |
| `⌘Z` | Undo the last assignment or merge |
| `esc` | Clear the selection |

The filter tabs limit the list to unsorted words, everything, or a single group,
which is how you revisit and recategorize words after the first pass.

**Merge** combines the selected rows into one word, and **Unmerge** splits a
merged row back apart. Unmerged words are remembered as standalone, so
`build_families.py` will not merge them again. If the rows are in different groups you are asked which
group the merged word should keep.

## Adding more words

Drop another spreadsheet with the same `Rank / Spanish / English / Frequency`
columns into the project and import it. Existing rows keep the group they were
already given, so re-importing or extending the list never erases sorting.

```bash
.venv/bin/python scripts/import_words.py Spanish_Words_1001_2000.xlsx
```

Then look for variants of the new words. The script prints what it would do and
writes nothing until you pass `--apply`; words already in a family are left as
they are, so it is safe to run repeatedly.

```bash
.venv/bin/python scripts/build_families.py
.venv/bin/python scripts/build_families.py --apply
```
