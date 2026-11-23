"""Persistent FSRS study sessions for word families."""

import random
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from fsrs import Card, FSRS, Rating, State

from app.store import ARCHIVED_GROUPS, GROUP_LABELS, GROUPS


ACTIVE_GROUPS = {"1", "2", "3", "4", "v"}
# A family is mature once FSRS expects it to stay remembered for three weeks.
MATURE_STABILITY_DAYS = 21
NEW_WEIGHTS = {"3": 0.40, "4": 0.40, "2": 0.10, "1": 0.05, "v": 0.05}
RATINGS = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(timezone.utc).isoformat() if value else None


def parse_time(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    # Postgres may trim trailing microsecond zeroes (for example, five digits).
    # Python 3.9's fromisoformat only accepts specific fractional widths.
    match = re.fullmatch(r"(.*\.)(\d+)([+-]\d\d:\d\d)", value)
    if match:
        value = match.group(1) + match.group(2).ljust(6, "0")[:6] + match.group(3)
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def start_of_local_day(value: datetime) -> datetime:
    """Midnight at the start of `value`'s calendar day, in the server's time zone.

    Reviews scheduled days out come due at the start of their day, so a word
    reviewed at 10pm is ready first thing the next day rather than at 10pm.
    """
    local = value.astimezone()
    midnight = datetime(local.year, local.month, local.day).astimezone()
    return midnight.astimezone(timezone.utc)


def calendar_days_between(start: datetime, end: datetime) -> int:
    return (end.astimezone().date() - start.astimezone().date()).days


def serialize_card(card: Card) -> Dict:
    return {
        "due": iso(card.due),
        "stability": card.stability,
        "difficulty": card.difficulty,
        "elapsed_days": card.elapsed_days,
        "scheduled_days": card.scheduled_days,
        "reps": card.reps,
        "lapses": card.lapses,
        "state": card.state.value,
        "last_review": iso(getattr(card, "last_review", None)),
    }


def deserialize_card(data: Dict) -> Card:
    return Card(
        due=parse_time(data["due"]),
        stability=data.get("stability", 0),
        difficulty=data.get("difficulty", 0),
        elapsed_days=data.get("elapsed_days", 0),
        scheduled_days=data.get("scheduled_days", 0),
        reps=data.get("reps", 0),
        lapses=data.get("lapses", 0),
        state=State(data.get("state", State.New.value)),
        last_review=parse_time(data["last_review"]) if data.get("last_review") else None,
    )


def variant_view(word: Dict) -> Dict:
    return {
        "rank": word["rank"],
        "spanish": word["spanish"],
        "english": word["english"],
        "alt_english": word.get("alt_english") or "",
        "frequency": word["frequency"],
    }


def main_rank_of(ranks: List[int], words_by_rank: Dict[int, Dict]) -> int:
    """A family's main word is the one whose rank is the family_id.

    Merging and build_families set family_id to the most frequent member, so
    that is the default; choosing another main word just repoints family_id.
    """
    family_id = words_by_rank.get(ranks[0], {}).get("family_id")
    return family_id if family_id in ranks else min(ranks)


def main_first(variants: List[Dict], main_rank: int) -> List[Dict]:
    return sorted(variants, key=lambda v: (v["rank"] != main_rank, v["rank"]))


def build_entries(words: Iterable[Dict]) -> List[Dict]:
    """Collapse word families into one entry each, led by the family's main word.

    Every form keeps its own rank and frequency. Entries sort by their most
    frequent member, so a family stays where you'd expect it in the list.
    """
    families: Dict[int, List[Dict]] = {}
    for word in words:
        families.setdefault(word.get("family_id") or word["rank"], []).append(word)
    entries = []
    for key, members in families.items():
        members.sort(key=lambda word: word["rank"])
        ranks = [word["rank"] for word in members]
        main_rank = key if key in ranks else ranks[0]
        variants = main_first([variant_view(word) for word in members], main_rank)
        main = variants[0]
        entries.append(
            {
                "key": key,
                "main_rank": main_rank,
                "ranks": ranks,
                "spanish": main["spanish"],
                "english": main["english"],
                "alt_english": main["alt_english"],
                "frequency": sum(word["frequency"] or 0 for word in members) or None,
                "group_code": members[0].get("group_code"),
                # Archived families keep their level but never come up in study.
                "archived": any(word.get("archived") for word in members),
                # You've chosen to keep studying it or to archive it. Until
                # then it comes up with a halo and both options.
                "decided": any(word.get("decided_at") for word in members),
                "merged": len(members) > 1,
                # Main word first, then the subfamily in rank order.
                "variants": variants,
            }
        )
    return sorted(entries, key=lambda entry: entry["ranks"][0])


def weighted_new_entries(
    entries: Iterable[Dict], count: int, rng: random.Random
) -> List[Dict]:
    buckets = {group: [] for group in NEW_WEIGHTS}
    for entry in entries:
        if entry["group_code"] in buckets:
            buckets[entry["group_code"]].append(entry)

    selected = []
    while len(selected) < count:
        available = [group for group, items in buckets.items() if items]
        if not available:
            break
        group = rng.choices(
            available, weights=[NEW_WEIGHTS[group] for group in available], k=1
        )[0]
        index = rng.randrange(len(buckets[group]))
        selected.append(buckets[group].pop(index))
    return selected


class StudyService:
    def __init__(self, store, rng: Optional[random.Random] = None):
        self.store = store
        self.rng = rng or random.Random()
        self.scheduler = FSRS()

    def _repeat(self, card: Card, now: datetime) -> Dict:
        """FSRS options for each rating, except that Good on a brand-new word
        graduates it straight to review (days away) instead of a 10-minute
        learning step, so it does not come back in the same session."""
        options = self.scheduler.repeat(card, now=now)
        if card.state == State.New:
            good = options[Rating.Good].card
            days = self.scheduler.next_interval(good.stability)
            good.state = State.Review
            good.scheduled_days = days
            good.due = now + timedelta(days=days)
        # Reviews (days away) land on a calendar day; learning steps (minutes)
        # keep their exact time.
        for option in options.values():
            if option.card.state == State.Review:
                option.card.due = start_of_local_day(option.card.due)
        return options

    def _cards_by_rank(self) -> Dict[int, Dict]:
        return {row["rank"]: row for row in self.store.list_study_cards()}

    def _entry_due(self, entry: Dict, cards: Dict[int, Dict]) -> Optional[datetime]:
        dates = [
            parse_time(cards[rank]["due"]) for rank in entry["ranks"] if rank in cards
        ]
        return min(dates) if dates else None

    def overall_progress(self, now: Optional[datetime] = None) -> Dict:
        now = now or utcnow()
        entries = [
            entry for entry in build_entries(self.store.list_words())
            if entry["group_code"] in ACTIVE_GROUPS and not entry["archived"]
        ]
        cards = self._cards_by_rank()
        groups = []
        started_by_group: Dict[str, List[Dict]] = {code: [] for code in ACTIVE_GROUPS}
        started = learning = review = mature = due = due_day = due_week = reviews = 0

        for entry in entries:
            rows = [cards[rank] for rank in entry["ranks"] if rank in cards]
            if not rows:
                continue
            started += 1
            family_cards = [deserialize_card(row["card_data"]) for row in rows]
            family_reviews = max(row["review_count"] for row in rows)
            reviews += family_reviews
            next_due = min(parse_time(row["due"]) for row in rows)
            # Upcoming counts nest: the next 24 hours are part of this week.
            if next_due <= now:
                due += 1
            else:
                if next_due <= now + timedelta(days=1):
                    due_day += 1
                if next_due <= now + timedelta(days=7):
                    due_week += 1
            if any(card.state in (State.Learning, State.Relearning) for card in family_cards):
                learning += 1
                stage = "learning"
            else:
                review += 1
                stage = "young"
                if min(card.stability for card in family_cards) >= MATURE_STABILITY_DAYS:
                    mature += 1
                    stage = "mature"
            started_by_group[entry["group_code"]].append(
                {
                    "key": entry["key"],
                    "spanish": entry["spanish"],
                    "english": entry["english"],
                    "forms": len(entry["variants"]) - 1,
                    "stage": stage,
                    "next_due": iso(next_due),
                    "reviews": family_reviews,
                }
            )

        for code in sorted(ACTIVE_GROUPS):
            group_entries = [entry for entry in entries if entry["group_code"] == code]
            group_started = sum(
                any(rank in cards for rank in entry["ranks"]) for entry in group_entries
            )
            groups.append(
                {
                    "code": code,
                    "label": GROUP_LABELS[code],
                    "total": len(group_entries),
                    "started": group_started,
                    # Learning first, then whatever comes due soonest.
                    "cards": sorted(
                        started_by_group[code],
                        key=lambda card: (card["stage"] != "learning", card["next_due"]),
                    ),
                }
            )

        total = len(entries)
        return {
            "total": total,
            "started": started,
            "unseen": total - started,
            "learning": learning,
            "review": review,
            "mature": mature,
            # Learning + young + mature always adds up to started.
            "young": review - mature,
            "due": due,
            "due_day": due_day,
            "due_week": due_week,
            "reviews": reviews,
            "percent_started": round(started * 100 / total) if total else 0,
            "groups": groups,
        }

    def start_or_resume(self, now: Optional[datetime] = None) -> Dict:
        now = now or utcnow()
        active = self.store.get_active_session()
        if active:
            return self.session_state(active["id"], now)

        # One session per calendar day: once today's has ended, the next one
        # opens at midnight. A session counts for the day you finished it too,
        # so finishing one you started days ago doesn't open another today.
        latest = self.store.get_latest_session()
        if latest and any(
            latest.get(field)
            and calendar_days_between(parse_time(latest[field]), now) == 0
            for field in ("started_at", "completed_at")
        ):
            return {
                "status": "done_today",
                "session_id": latest["id"],
                "ended": latest["status"] == "abandoned",
                "next_session_at": iso(start_of_local_day(now + timedelta(days=1))),
                "progress": self._progress(self.store.list_session_queue(latest["id"])),
            }

        words = self.store.list_words()
        entries = [
            entry for entry in build_entries(words)
            if entry["group_code"] in ACTIVE_GROUPS and not entry["archived"]
        ]
        cards = self._cards_by_rank()
        due = [
            entry for entry in entries
            if self._entry_due(entry, cards) is not None
            and self._entry_due(entry, cards) <= now
        ]
        unseen = [
            entry for entry in entries
            if not any(rank in cards for rank in entry["ranks"])
        ]
        new = weighted_new_entries(unseen, 10, self.rng)

        items = [(entry, "due") for entry in due] + [
            (entry, "new") for entry in new
        ]
        self.rng.shuffle(items)

        session_id = str(uuid.uuid4())
        session = {
            "id": session_id,
            "status": "active",
            "started_at": iso(now),
            "completed_at": None,
        }
        queue = [
            {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "card_key": entry["key"],
                "ranks": entry["ranks"],
                "source": source,
                "position": position,
                "available_at": iso(now),
                "completed_at": None,
            }
            for position, (entry, source) in enumerate(items)
        ]
        self.store.create_study_session(session, queue)
        return self.session_state(session_id, now)

    def _intervals(
        self, ranks: List[int], cards: Dict[int, Dict], now: datetime
    ) -> Dict[str, str]:
        predicted: Dict[str, List[Card]] = {name: [] for name in RATINGS}
        for rank in ranks:
            card = (
                deserialize_card(cards[rank]["card_data"])
                if rank in cards
                else Card(due=now)
            )
            options = self._repeat(card, now)
            for name, rating in RATINGS.items():
                predicted[name].append(options[rating].card)
        labels = {}
        for name, family_cards in predicted.items():
            soonest = min(family_cards, key=lambda card: card.due)
            if soonest.state == State.Review:
                labels[name] = "%dd" % calendar_days_between(now, soonest.due)
            else:
                # A learning step of a few minutes: the card comes back later
                # in this session.
                labels[name] = "repeat"
        return labels

    def session_state(
        self, session_id: str, now: Optional[datetime] = None,
        queue: Optional[List[Dict]] = None,
    ) -> Dict:
        """The session's next card. `queue` is passed by review(), which has
        just read and updated it, to save another round trip to the database."""
        now = now or utcnow()
        if queue is None:
            active = self.store.get_active_session()
            if not active or active["id"] != session_id:
                return {"status": "complete", "session_id": session_id}
            queue = self.store.list_session_queue(session_id)
        pending = [row for row in queue if row["completed_at"] is None]
        if not pending:
            self.store.set_session_status(session_id, "completed", iso(now))
            return {
                "status": "complete",
                "session_id": session_id,
                "progress": self._progress(queue),
            }

        current = self._next_row(pending, now)
        # Only this card's words and FSRS state, fetched side by side: reading
        # every word just to show one card made each rating slow.
        with ThreadPoolExecutor(max_workers=2) as pool:
            words_job = pool.submit(self.store.get_words, current["ranks"])
            cards_job = pool.submit(self.store.get_study_cards, current["ranks"])
            words = {word["rank"]: word for word in words_job.result()}
            cards = {row["rank"]: row for row in cards_job.result()}
        main_rank = main_rank_of(current["ranks"], words)
        variants = main_first(
            [variant_view(words[rank]) for rank in current["ranks"] if rank in words],
            main_rank,
        )
        return {
            "status": "active",
            "session_id": session_id,
            "queue_id": current["id"],
            "source": current["source"],
            # Still waiting on keep-or-archive: shown with a halo and both
            # options, every time it comes up, until you choose.
            "needs_decision": not any(word.get("decided_at") for word in words.values()),
            "card_key": current["card_key"],
            "ranks": current["ranks"],
            "main_rank": main_rank,
            "group_code": words[current["ranks"][0]].get("group_code"),
            # Main word first, then the subfamily.
            "variants": variants,
            "intervals": self._intervals(current["ranks"], cards, now),
            "progress": self._progress(queue),
        }

    @staticmethod
    def _next_row(pending: List[Dict], now: datetime) -> Dict:
        """The card to show next.

        Cards go in queue order; a short repeat sits at the back of the queue
        and is skipped while its few-minute step is still running. Once only
        repeats are left, the soonest one is shown right away rather than
        making you wait for it.
        """
        available = [row for row in pending if parse_time(row["available_at"]) <= now]
        if available:
            return min(available, key=lambda row: (row["position"], row["available_at"]))
        return min(pending, key=lambda row: (parse_time(row["available_at"]), row["position"]))

    @staticmethod
    def _progress(queue: List[Dict]) -> Dict:
        initial = [row for row in queue if row["source"] != "repeat"]
        return {
            "completed": sum(row["completed_at"] is not None for row in queue),
            "remaining": sum(row["completed_at"] is None for row in queue),
            "total": len(queue),
            "due": sum(row["source"] == "due" for row in initial),
            "new": sum(row["source"] == "new" for row in initial),
            "repeats": sum(row["source"] == "repeat" for row in queue),
            # What's still ahead of you, counting down as you review.
            "left": {
                source: sum(
                    row["source"] == source and row["completed_at"] is None
                    for row in queue
                )
                for source in ("due", "new", "repeat")
            },
        }

    def review(
        self, session_id: str, queue_id: str, rating_name: str,
        now: Optional[datetime] = None
    ) -> Dict:
        now = now or utcnow()
        if rating_name not in RATINGS:
            raise ValueError("Unknown rating")
        queue = self.store.list_session_queue(session_id)
        current = next((row for row in queue if row["id"] == queue_id), None)
        pending = [row for row in queue if row["completed_at"] is None]
        expected = self._next_row(pending, now) if pending else None
        if (
            current is None
            or current["completed_at"] is not None
            or expected is None
            or expected["id"] != queue_id
        ):
            raise ValueError("That card is not available for review")

        cards = {row["rank"]: row for row in self.store.get_study_cards(current["ranks"])}
        rating = RATINGS[rating_name]
        rows = []
        next_dates = []
        learning = False
        for rank in current["ranks"]:
            card = (
                deserialize_card(cards[rank]["card_data"])
                if rank in cards
                else Card(due=now)
            )
            result = self._repeat(card, now)[rating]
            updated = result.card
            learning = learning or updated.state in (State.Learning, State.Relearning)
            next_dates.append(updated.due)
            rows.append(
                {
                    "rank": rank,
                    "card_data": serialize_card(updated),
                    "due": iso(updated.due),
                    "last_review": iso(getattr(updated, "last_review", now)),
                    "review_count": updated.reps,
                    "updated_at": iso(now),
                }
            )

        next_due = min(next_dates)
        review_log = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "card_key": current["card_key"],
            "ranks": current["ranks"],
            "rating": rating.value,
            "source": current["source"],
            "reviewed_at": iso(now),
            "next_due": iso(next_due),
        }
        repeat = None
        if learning:
            repeat = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "card_key": current["card_key"],
                "ranks": current["ranks"],
                "source": "repeat",
                "position": max((row["position"] for row in queue), default=-1) + 1,
                "available_at": iso(next_due),
                "completed_at": None,
            }

        # These writes don't depend on each other, so they go out together.
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [
                pool.submit(self.store.upsert_study_cards, rows),
                pool.submit(self.store.complete_queue_item, queue_id, iso(now)),
                pool.submit(self.store.insert_review_log, review_log),
            ]
            if repeat:
                jobs.append(pool.submit(self.store.append_session_queue, repeat))
            for job in jobs:
                job.result()

        # The queue as it now stands, without reading it back.
        queue = [
            dict(row, completed_at=iso(now)) if row["id"] == queue_id else row
            for row in queue
        ]
        if repeat:
            queue.append(repeat)
        return self.session_state(session_id, now, queue=queue)

    def update_variant(
        self, rank: int, spanish: str, english: str, alt_english: Optional[str] = None
    ) -> None:
        if not spanish.strip() or not english.strip():
            raise ValueError("Spanish and English are required")
        alt = (alt_english or "").strip() or None
        if not self.store.update_word(rank, spanish.strip(), english.strip(), alt):
            raise ValueError("Unknown rank")

    def set_main_word(self, rank: int) -> List[int]:
        """Make this word the main word of its family."""
        words = self.store.list_words()
        by_rank = {word["rank"]: word for word in words}
        if rank not in by_rank:
            raise ValueError("Unknown rank")
        family = by_rank[rank].get("family_id")
        if not family:
            return [rank]
        ranks = [word["rank"] for word in words if word.get("family_id") == family]
        self.store.set_family(ranks, rank)
        return ranks

    def change_group(
        self, session_id: str, ranks: List[int], group_code: str,
        now: Optional[datetime] = None
    ) -> Dict:
        if group_code not in GROUPS:
            raise ValueError("Unknown group")
        now = now or utcnow()
        words = self.store.list_words()
        by_rank = {word["rank"]: word for word in words}
        family_keys = {
            by_rank[rank].get("family_id") or rank for rank in ranks if rank in by_rank
        }
        expanded = [
            word["rank"] for word in words
            if (word.get("family_id") or word["rank"]) in family_keys
        ]
        if not expanded:
            raise ValueError("Unknown word")
        self.store.assign(expanded, group_code)
        if group_code in ARCHIVED_GROUPS:
            self.store.complete_pending_for_ranks(session_id, expanded, iso(now))
        return self.session_state(session_id, now)

    def _family_ranks(self, ranks: List[int]) -> List[int]:
        words = self.store.list_words()
        by_rank = {word["rank"]: word for word in words}
        family_keys = {
            by_rank[rank].get("family_id") or rank for rank in ranks if rank in by_rank
        }
        expanded = [
            word["rank"] for word in words
            if (word.get("family_id") or word["rank"]) in family_keys
        ]
        if not expanded:
            raise ValueError("Unknown word")
        return expanded

    def keep(self, ranks: List[int], now: Optional[datetime] = None) -> List[int]:
        """You chose to keep studying these families: no more halo or prompt."""
        expanded = self._family_ranks(ranks)
        self.store.mark_decided(expanded, iso(now or utcnow()))
        return expanded

    def set_archived(
        self, ranks: List[int], archived: bool, now: Optional[datetime] = None,
        reset_decision: bool = False,
    ) -> List[int]:
        """Archive or unarchive whole families. Archived words leave any study
        session under way and are never picked again. Archiving also settles
        the keep-or-archive choice."""
        now = now or utcnow()
        expanded = self._family_ranks(ranks)
        self.store.set_archived(expanded, archived)
        if reset_decision:
            self.store.clear_decided(expanded)
        if archived:
            self.store.mark_decided(expanded, iso(now))
            active = self.store.get_active_session()
            if active:
                self.store.complete_pending_for_ranks(active["id"], expanded, iso(now))
        return expanded

    def close_session(self, session_id: str, status: str) -> None:
        if status not in {"completed", "abandoned"}:
            raise ValueError("Invalid session status")
        self.store.set_session_status(session_id, status, iso(utcnow()))
