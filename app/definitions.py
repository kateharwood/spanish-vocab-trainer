"""Ask Claude, in the background, for clearer English definitions of new words.

When a study session starts, every word family it introduces for the first
time is sent to Claude once. The answer is stored as a suggestion per word,
which the study card offers to accept or dismiss. Nothing changes a word's
English until you accept.

Set ANTHROPIC_API_KEY (in .env is fine) to turn this on; without it the
feature stays off and the app works as before.
"""

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"

# Suggestions that are settled and should not be requested again. A failed
# request is retried the next time its word shows up as new.
SETTLED = {"pending", "accepted", "dismissed", "unchanged"}

SYSTEM_PROMPT = """\
You maintain the English side of a Spanish-to-English flashcard deck built \
from a word-frequency list. The English glosses were generated automatically, \
so some are vague, miss a common sense, mislead without context, are oddly \
formatted, or are simply wrong.

The learner reads the English and has to recall the Spanish, so a gloss has \
to lead to this particular Spanish word rather than a near synonym.

Each form has two fields:
- english: the main definition, the one meaning a learner should think of \
first. Keep it short, a few words, and never use ";" in it. Synonyms for \
that one meaning are separated by commas ("to decrease, diminish").
- alt_english: other meanings worth knowing, shown smaller under the main \
one, most common first, separated by "; " (for pelotas: "(vulgar) \
testicles"). Use an empty string when there is no other meaning worth \
learning.

For each Spanish form you are given:
- Add short context in parentheses only when it is needed to land on the \
right word: part of speech when ambiguous, register (formal, colloquial, \
Latin America, Spain), gender or number when other forms of the same word \
are on the card, or the verb form for gerunds and participles.
- Correct mistakes and tidy formatting (stray punctuation, inconsistent \
slashes, capitalization).
- If a form's definitions are already good, return them exactly as given \
with changed set to false. Do not reword good definitions.

Also write a one-sentence note for the learner saying what you changed and \
why. Leave the note empty when nothing changed."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer"},
                    "english": {"type": "string"},
                    "alt_english": {"type": "string"},
                    "changed": {"type": "boolean"},
                },
                "required": ["rank", "english", "alt_english", "changed"],
                "additionalProperties": False,
            },
        },
        "note": {"type": "string"},
    },
    "required": ["variants", "note"],
    "additionalProperties": False,
}


def split_definition(english: str) -> Optional[Tuple[str, str]]:
    """(main, alt) split at the first ";" outside parentheses, or None.

    "came; wine" -> ("came", "wine"); "worried (masc.; formal)" -> None.
    """
    depth = 0
    for index, char in enumerate(english):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            main, alt = english[:index].strip(), english[index + 1:].strip()
            return (main, alt) if main and alt else None
    return None


def main_and_alt(english: str, alt: Optional[str]) -> Tuple[str, str]:
    """Keep the main definition to one meaning: anything after a top-level ";"
    moves to the front of the alt definition."""
    alt = (alt or "").strip()
    split = split_definition(english or "")
    if not split:
        return (english or "").strip(), alt
    main, rest = split
    return main, "; ".join(part for part in (rest, alt) if part)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_prompt(variants: List[Dict]) -> str:
    lines = [
        "rank %d | spanish: %s | current english: %s | current alt_english: %s"
        % (v["rank"], v["spanish"], v["english"], v.get("alt_english") or "")
        for v in variants
    ]
    family = (
        "These forms are one card (variants of the same word), so each gloss "
        "should make clear which form it is.\n\n"
        if len(variants) > 1
        else ""
    )
    return (
        family
        + "Rank is the word's position in the frequency list (1 = most common).\n\n"
        + "\n".join(lines)
    )


def anthropic_suggester() -> Optional[Callable[[List[Dict]], Dict]]:
    """A function that asks Claude about one family, or None when unconfigured."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    import anthropic

    client = anthropic.Anthropic()

    def suggest(variants: List[Dict]) -> Dict:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(variants)}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            # If a safety classifier declines, retry on Anthropic's
            # recommended fallback model instead of failing.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to suggest a definition")
        text = next(block.text for block in response.content if block.type == "text")
        return json.loads(text)

    return suggest


class DefinitionHelper:
    def __init__(
        self,
        store,
        suggest: Optional[Callable[[List[Dict]], Dict]] = None,
        executor: Optional[ThreadPoolExecutor] = None,
    ):
        self.store = store
        self.suggest = suggest
        self.executor = executor or ThreadPoolExecutor(max_workers=3)
        self._in_flight = set()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.suggest is not None

    def is_in_flight(self, ranks: Iterable[int]) -> bool:
        with self._lock:
            return any(rank in self._in_flight for rank in ranks)

    def queue_for_session(self, session_id: str) -> int:
        """Request suggestions for the session's new cards that have none yet."""
        if not self.enabled:
            return 0
        rows = [
            row for row in self.store.list_session_queue(session_id)
            if row["source"] == "new" and row["completed_at"] is None
        ]
        return self.queue_families([row["ranks"] for row in rows])

    def queue_families(self, families: List[List[int]]) -> int:
        """Request suggestions for each family (a list of ranks) that has none yet."""
        if not self.enabled or not families:
            return 0
        wanted = sorted({rank for ranks in families for rank in ranks})
        settled = {
            row["rank"] for row in self.store.get_suggestions(wanted)
            if row["status"] in SETTLED
        }
        words = {word["rank"]: word for word in self.store.list_words()}
        queued = 0
        for ranks in families:
            if any(rank in settled for rank in ranks):
                continue
            variants = [
                {
                    "rank": rank,
                    "spanish": words[rank]["spanish"],
                    "english": words[rank]["english"],
                    "alt_english": words[rank].get("alt_english") or "",
                }
                for rank in ranks if rank in words
            ]
            with self._lock:
                if not variants or any(v["rank"] in self._in_flight for v in variants):
                    continue
                self._in_flight.update(v["rank"] for v in variants)
            self.executor.submit(self._run, variants)
            queued += 1
        return queued

    def _run(self, variants: List[Dict]) -> None:
        try:
            result = self.suggest(variants)
            by_rank = {item["rank"]: item for item in result.get("variants", [])}
            note = (result.get("note") or "").strip()
            rows = []
            for variant in variants:
                item = by_rank.get(variant["rank"]) or {}
                suggested, suggested_alt = main_and_alt(
                    item.get("english") or "", item.get("alt_english")
                )
                current_alt = variant.get("alt_english") or ""
                changed = bool(
                    item.get("changed") and suggested
                    and (suggested != variant["english"] or suggested_alt != current_alt)
                )
                rows.append(
                    {
                        "rank": variant["rank"],
                        "original_english": variant["english"],
                        "original_alt": current_alt or None,
                        "suggested_english": suggested if changed else variant["english"],
                        "suggested_alt": (suggested_alt if changed else current_alt) or None,
                        "note": note if changed else "",
                        "status": "pending" if changed else "unchanged",
                        "updated_at": utcnow_iso(),
                    }
                )
            self.store.upsert_suggestions(rows)
        except Exception:
            log.exception("Definition suggestion failed for %s", [v["rank"] for v in variants])
            self.store.upsert_suggestions(
                [
                    {
                        "rank": variant["rank"],
                        "original_english": variant["english"],
                        "original_alt": variant.get("alt_english") or None,
                        "suggested_english": None,
                        "suggested_alt": None,
                        "note": "",
                        "status": "failed",
                        "updated_at": utcnow_iso(),
                    }
                    for variant in variants
                ]
            )
        finally:
            with self._lock:
                self._in_flight.difference_update(v["rank"] for v in variants)

    def for_ranks(self, ranks: List[int]) -> Dict:
        rows = self.store.get_suggestions(ranks)
        return {
            "enabled": self.enabled,
            "working": self.is_in_flight(ranks),
            "suggestions": [
                {
                    "rank": row["rank"],
                    "original_english": row["original_english"],
                    "original_alt": row.get("original_alt") or "",
                    "suggested_english": row["suggested_english"],
                    "suggested_alt": row.get("suggested_alt") or "",
                    "note": row["note"],
                }
                for row in rows if row["status"] == "pending"
            ],
        }

    def resolve(self, ranks: List[int], accept: bool) -> List[Dict]:
        """Accept (apply) or dismiss the pending suggestions for these ranks."""
        rows = [
            row for row in self.store.get_suggestions(ranks) if row["status"] == "pending"
        ]
        if accept:
            words = {word["rank"]: word for word in self.store.list_words()}
            for row in rows:
                word = words.get(row["rank"])
                if word:
                    # Suggestions saved before alt definitions existed can
                    # still pack several meanings into the main one.
                    row["suggested_english"], row["suggested_alt"] = main_and_alt(
                        row["suggested_english"], row.get("suggested_alt")
                    )
                    self.store.update_word(
                        row["rank"], word["spanish"], row["suggested_english"],
                        row["suggested_alt"] or None,
                    )
        for row in rows:
            self.store.set_suggestion_status(
                row["rank"], "accepted" if accept else "dismissed", utcnow_iso()
            )
        return [
            {
                "rank": row["rank"],
                "english": row["suggested_english"],
                "alt_english": row.get("suggested_alt") or "",
            }
            for row in rows
        ] if accept else []
