# Hasta Cinco Mil

*Hasta cinco mil*: up to five thousand. About 5,000 of the most common Spanish
words, plus words Claude added, which you sort into levels and then study a few
at a time with spaced repetition (FSRS). Sorting is a one-time pass, but any
word can be moved between levels later.

## Groups

| Code | Meaning | Study behavior |
| --- | --- | --- |
| `0a` | Level 0 | Never studied |
| `1` | Level 1 | Shows up sometimes |
| `2` | Level 2 | Shows up the most |
| `3` | Level 3 | Shows up the most |
| `4` | Level 4 | Shows up the most, a few at a time |
| `n` | Bad entries | Never studied |
| `0b` | Don't care to learn | Never studied |
| `v` | Verb practice: know it, want to drill it | Shows up sometimes |

Words start with no group until they are sorted.

## Archived words

Separately from its group, any word family can be **archived**. It keeps its
group but never comes up in study again, and leaves a session already under
way. The List has two sections, **Unarchived** and **Archived**, each with the
usual group filters inside.

A word you haven't decided on yet comes up with a blue halo and a bar asking
whether to **Keep** (`k`) studying it or **Archive** (`a`) it, in Sort and in
Study. Like a suggested definition, the bar keeps coming back until you choose,
and never again after that; skipping a word doesn't count. In Sort, a level can
only be picked after Keep. In the List, giving a word a level counts as Keep and
archiving it counts as Archive. ⌘Z after either brings the question back.
Unarchive from List → Archived, or from Sort by going back to the card.
With Supabase, run
`db/archive_migration.sql` in the SQL editor once.

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

One unsorted, unarchived word at a time in rank order, Spanish first. The translation stays
hidden until you reveal it, so your honest reaction decides the group. Once
revealed, a new word first asks **Keep** or **Archive**; only a kept word goes
on to a level. The card then shows where the word can go, grouped into **Study it**
(Levels 1–4, Verbs) and **Don't study** (Know it, Don't care, Bad entry). Click
one or use its key. Undo sits next to back and skip.

| Key | Result |
| --- | --- |
| `space` (or click the card) | Reveal the English |
| `0` | Assign to Level 0, then advance |
| `1`–`4` | Assign to Levels 1–4, then advance |
| `d` | Assign to Don't care to learn, then advance |
| `b` | Assign to Bad entries, then advance |
| `v` | Assign to `v`, then advance |
| `u` | Clear the group, then advance |
| `a` | Archive (or unarchive), then advance |
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
| `a` | Archive the selection (Unarchive in the Archived section) |
| `⌘Z` | Undo the last assignment, archive, or merge |
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

## Deploying to Vercel

The app runs on Vercel as a single Python function (`app/main.py`), with the
data in Supabase. `vercel.json` gives each request up to 5 minutes, since the
requests that ask Claude for definitions wait for its answer there.

1. Run `db/deploy_migration.sql` in the Supabase SQL editor once.
2. In Vercel, **Add New → Project** and import this GitHub repo. No build
   settings are needed.
3. Under **Settings → Environment Variables**, add:

   | Name | Value |
   | --- | --- |
   | `SUPABASE_URL` | same as your `.env` |
   | `SUPABASE_SECRET_KEY` | same as your `.env` |
   | `ANTHROPIC_API_KEY` | same as your `.env` (optional) |
   | `APP_PASSWORD` | the password for the login page |
   | `APP_TIMEZONE` | your time zone, e.g. `America/New_York` |

   Without `APP_PASSWORD` the deployed site refuses to run, so it is never
   public by accident.
4. Deploy, then under **Settings → Domains** add your domain and set the DNS
   records Vercel shows at your registrar.

Signing in lasts a year per device. Changing `APP_PASSWORD` signs every device
out. Run locally without `APP_PASSWORD` and there is no login.
