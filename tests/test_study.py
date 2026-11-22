import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fsrs import Card, State

from app.store import SqliteStore
from app.study import (
    StudyService,
    iso,
    parse_time,
    serialize_card,
    start_of_local_day,
    weighted_new_entries,
)


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SqliteStore(self.temp.name + "/test.db")

    def tearDown(self):
        self.temp.cleanup()

    def add_words(self, groups):
        rows = []
        for rank, group in enumerate(groups, 1):
            rows.append(
                {
                    "rank": rank,
                    "spanish": "es-%d" % rank,
                    "english": "en-%d" % rank,
                    "frequency": 1000 - rank,
                    "group_code": group,
                }
            )
        self.store.upsert_words(rows)

    def clear_study(self):
        with self.store._connect() as conn:
            conn.execute("delete from review_logs")
            conn.execute("delete from study_session_queue")
            conn.execute("delete from study_sessions")
            conn.execute("delete from study_cards")

    def test_weighted_new_selection_converges(self):
        entries = []
        for group in ("1", "2", "3", "4", "v"):
            entries.extend(
                {"key": group + str(i), "group_code": group}
                for i in range(100)
            )
        counts = {group: 0 for group in ("1", "2", "3", "4", "v")}
        rng = random.Random(10)
        for _ in range(1000):
            for entry in weighted_new_entries(entries, 10, rng):
                counts[entry["group_code"]] += 1
        proportions = {group: value / 10000 for group, value in counts.items()}
        self.assertAlmostEqual(proportions["3"], 0.40, delta=0.03)
        self.assertAlmostEqual(proportions["4"], 0.40, delta=0.03)
        self.assertAlmostEqual(proportions["2"], 0.10, delta=0.03)
        self.assertAlmostEqual(proportions["1"], 0.05, delta=0.03)
        self.assertAlmostEqual(proportions["v"], 0.05, delta=0.03)

    def test_postgres_fractional_timestamp_parses_on_python_39(self):
        parsed = parse_time("2026-09-12T02:51:42.19873+00:00")
        self.assertEqual(parsed.microsecond, 198730)

    def test_overall_progress_summarizes_study_cards(self):
        self.add_words(["1", "2", "3", "0a"])
        cards = [
            Card(
                due=NOW - timedelta(days=1),
                stability=30,
                difficulty=5,
                reps=4,
                state=State.Review,
                last_review=NOW - timedelta(days=10),
            ),
            Card(
                due=NOW + timedelta(minutes=5),
                stability=1,
                difficulty=5,
                reps=1,
                state=State.Learning,
                last_review=NOW,
            ),
        ]
        self.store.upsert_study_cards(
            [
                {
                    "rank": rank,
                    "card_data": serialize_card(card),
                    "due": iso(card.due),
                    "last_review": iso(card.last_review),
                    "review_count": card.reps,
                    "updated_at": iso(NOW),
                }
                for rank, card in enumerate(cards, 1)
            ]
        )

        progress = StudyService(self.store).overall_progress(NOW)

        self.assertEqual(progress["total"], 3)
        self.assertEqual(progress["started"], 2)
        self.assertEqual(progress["unseen"], 1)
        self.assertEqual(progress["due"], 1)
        self.assertEqual(progress["learning"], 1)
        self.assertEqual(progress["review"], 1)
        self.assertEqual(progress["mature"], 1)
        self.assertEqual(progress["young"], 0)
        self.assertEqual(
            progress["learning"] + progress["young"] + progress["mature"],
            progress["started"],
        )
        # The learning card is due in 5 minutes: within the next day, and so
        # within the week too.
        self.assertEqual(progress["due_day"], 1)
        self.assertEqual(progress["due_week"], 1)
        by_group = {g["code"]: g["cards"] for g in progress["groups"]}
        self.assertEqual(
            [(c["spanish"], c["stage"]) for c in by_group["1"]], [("es-1", "mature")]
        )
        self.assertEqual(
            [(c["spanish"], c["stage"]) for c in by_group["2"]], [("es-2", "learning")]
        )
        self.assertEqual(by_group["3"], [])
        self.assertEqual(progress["reviews"], 5)

    def test_weighted_selection_redistributes_empty_buckets(self):
        entries = [{"key": i, "group_code": "4"} for i in range(6)]
        chosen = weighted_new_entries(entries, 10, random.Random(1))
        self.assertEqual(len(chosen), 6)
        self.assertEqual({entry["group_code"] for entry in chosen}, {"4"})

    def test_session_has_all_due_plus_ten_new_and_resumes(self):
        self.add_words(["1"] * 13 + ["0a", "n", "0b"])
        old = Card(
            due=NOW - timedelta(days=1),
            stability=3,
            difficulty=5,
            reps=2,
            state=State.Review,
            last_review=NOW - timedelta(days=4),
        )
        self.store.upsert_study_cards(
            [
                {
                    "rank": 1,
                    "card_data": serialize_card(old),
                    "due": iso(old.due),
                    "last_review": iso(old.last_review),
                    "review_count": old.reps,
                    "updated_at": iso(NOW),
                }
            ]
        )
        service = StudyService(self.store, random.Random(4))
        state = service.start_or_resume(NOW)
        self.assertEqual(state["progress"]["due"], 1)
        self.assertEqual(state["progress"]["new"], 10)
        self.assertEqual(state["progress"]["total"], 11)
        resumed = service.start_or_resume(NOW)
        self.assertEqual(resumed["session_id"], state["session_id"])
        self.assertEqual(
            [row["id"] for row in self.store.list_session_queue(state["session_id"])],
            [row["id"] for row in self.store.list_session_queue(resumed["session_id"])],
        )

    def test_again_creates_short_repeat(self):
        self.add_words(["3"])
        service = StudyService(self.store, random.Random(2))
        state = service.start_or_resume(NOW)
        self.assertEqual(state["intervals"]["again"], "repeat")
        self.assertEqual(state["intervals"]["hard"], "repeat")
        after = service.review(
            state["session_id"], state["queue_id"], "again", NOW
        )
        # Nothing else is left, so the repeat comes straight back: no waiting.
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["source"], "repeat")
        self.assertEqual(after["progress"]["repeats"], 1)

    def test_family_review_updates_every_rank(self):
        self.add_words(["2", "2"])
        self.store.set_family([1, 2], 1)
        service = StudyService(self.store, random.Random(3))
        state = service.start_or_resume(NOW)
        self.assertEqual(state["ranks"], [1, 2])
        service.review(state["session_id"], state["queue_id"], "easy", NOW)
        cards = self.store.list_study_cards()
        self.assertEqual({row["rank"] for row in cards}, {1, 2})
        self.assertEqual(len({row["due"] for row in cards}), 1)

    def test_edit_and_archive_from_study_card(self):
        self.add_words(["4", "4"])
        self.store.set_family([1, 2], 1)
        service = StudyService(self.store, random.Random(5))
        state = service.start_or_resume(NOW)
        service.update_variant(1, "corregido", "corrected")
        word = self.store.list_words()[0]
        self.assertEqual((word["spanish"], word["english"]), ("corregido", "corrected"))
        result = service.change_group(
            state["session_id"], [1], "0a", NOW
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            {word["group_code"] for word in self.store.list_words()}, {"0a"}
        )

    def test_review_rejects_a_later_queue_item(self):
        self.add_words(["1", "2"])
        service = StudyService(self.store, random.Random(8))
        state = service.start_or_resume(NOW)
        queue = self.store.list_session_queue(state["session_id"])
        later = next(row for row in queue if row["id"] != state["queue_id"])
        with self.assertRaisesRegex(ValueError, "not available"):
            service.review(state["session_id"], later["id"], "good", NOW)

    def test_good_on_a_new_word_schedules_days_out(self):
        self.add_words(["3"])
        service = StudyService(self.store, random.Random(2))
        state = service.start_or_resume(NOW)
        self.assertEqual(state["intervals"]["good"], "3d")
        after = service.review(state["session_id"], state["queue_id"], "good", NOW)
        self.assertEqual(after["status"], "complete")
        card = self.store.list_study_cards()[0]
        self.assertEqual(card["card_data"]["state"], State.Review.value)
        self.assertEqual(parse_time(card["due"]), start_of_local_day(NOW + timedelta(days=3)))

    def test_reviews_come_due_at_the_start_of_a_calendar_day(self):
        import os
        import time

        previous = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            self.add_words(["3"])
            service = StudyService(self.store, random.Random(2))
            # 10pm on Nov 9 in New York.
            late = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
            state = service.start_or_resume(late)
            self.assertEqual(state["intervals"]["good"], "3d")
            service.review(state["session_id"], state["queue_id"], "good", late)
            due = parse_time(self.store.list_study_cards()[0]["due"])
            # Midnight on Nov 12 New York time, not 10pm.
            self.assertEqual(due, datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc))
        finally:
            if previous is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = previous
            time.tzset()

    def test_only_one_session_per_calendar_day(self):
        self.add_words(["3", "3"])
        service = StudyService(self.store, random.Random(2))
        first = service.start_or_resume(NOW)
        state = first
        while state["status"] == "active":
            state = service.review(state["session_id"], state["queue_id"], "easy", NOW)
        self.assertEqual(state["status"], "complete")

        later_today = service.start_or_resume(NOW + timedelta(hours=1))
        self.assertEqual(later_today["status"], "done_today")
        self.assertEqual(later_today["session_id"], first["session_id"])
        self.assertEqual(
            parse_time(later_today["next_session_at"]),
            start_of_local_day(NOW + timedelta(days=1)),
        )

        tomorrow = service.start_or_resume(start_of_local_day(NOW + timedelta(days=1)))
        self.assertNotEqual(tomorrow["session_id"], first["session_id"])

    def test_ending_a_session_early_still_counts_for_the_day(self):
        self.add_words(["3", "3"])
        service = StudyService(self.store, random.Random(2))
        first = service.start_or_resume(NOW)
        service.close_session(first["session_id"], "abandoned")
        again = service.start_or_resume(NOW + timedelta(minutes=5))
        self.assertEqual(again["status"], "done_today")
        self.assertTrue(again["ended"])

    def test_repeat_comes_back_after_the_remaining_cards(self):
        self.add_words(["3", "3", "3"])
        service = StudyService(self.store, random.Random(2))
        state = service.start_or_resume(NOW)
        missed = state["ranks"]
        state = service.review(state["session_id"], state["queue_id"], "hard", NOW)
        seen = []
        while state["status"] == "active":
            seen.append((state["source"], state["ranks"]))
            state = service.review(state["session_id"], state["queue_id"], "easy", NOW)
        # The other two new cards first, then the repeat, all at the same moment.
        self.assertEqual([source for source, _ in seen], ["new", "new", "repeat"])
        self.assertEqual(seen[-1][1], missed)
        self.assertEqual(state["status"], "complete")


    def test_family_is_led_by_its_main_word(self):
        from app.study import build_entries

        self.add_words(["2", "2", "2"])
        self.store.set_family([1, 2, 3], 1)
        self.store.update_word(1, "es-1", "en-1", "other sense")
        entry = build_entries(self.store.list_words())[0]
        self.assertEqual(entry["main_rank"], 1)
        self.assertEqual((entry["spanish"], entry["english"], entry["alt_english"]),
                         ("es-1", "en-1", "other sense"))
        self.assertEqual([v["rank"] for v in entry["variants"]], [1, 2, 3])

        service = StudyService(self.store, random.Random(1))
        self.assertEqual(sorted(service.set_main_word(3)), [1, 2, 3])
        entry = build_entries(self.store.list_words())[0]
        # The main word changes, the family keeps its place and all its forms.
        self.assertEqual(entry["main_rank"], 3)
        self.assertEqual(entry["spanish"], "es-3")
        self.assertEqual([v["rank"] for v in entry["variants"]], [3, 1, 2])
        self.assertEqual(entry["ranks"], [1, 2, 3])

        state = service.start_or_resume(NOW)
        self.assertEqual(state["main_rank"], 3)
        self.assertEqual([v["rank"] for v in state["variants"]], [3, 1, 2])

    def test_split_definition_at_the_first_top_level_semicolon(self):
        from app.definitions import split_definition as split
        self.assertEqual(split("came; wine"), ("came", "wine"))
        self.assertEqual(
            split("purpose; intention; (a propósito) by the way"),
            ("purpose", "intention; (a propósito) by the way"),
        )
        self.assertEqual(split("worried (masc.; formal)"), None)
        self.assertEqual(split("to stop/arrest"), None)


class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        import app.main as main

        self.temp = tempfile.TemporaryDirectory()
        self.store = SqliteStore(self.temp.name + "/test.db")
        self.store.upsert_words(
            {"rank": rank, "spanish": "es-%d" % rank, "english": "en-%d" % rank,
             "frequency": 100 - rank, "group_code": "2"}
            for rank in (1, 2, 3)
        )
        self.originals = (main.store, main.study)
        main.store = self.store
        main.study = StudyService(self.store, random.Random(1))
        self.main = main
        self.client = TestClient(main.app)

    def tearDown(self):
        self.main.store, self.main.study = self.originals
        self.temp.cleanup()

    def test_archiving_from_list_removes_word_from_active_session(self):
        session = self.client.post("/api/study/session").json()
        self.client.post("/api/assign", json={"ranks": [1, 2, 3], "group_code": "0a"})
        queue = self.store.list_session_queue(session["session_id"])
        self.assertTrue(all(row["completed_at"] for row in queue))

    def test_unmerged_words_become_families_of_one(self):
        self.client.post("/api/merge", json={"ranks": [1, 2]})
        self.client.post("/api/unmerge", json={"ranks": [1]})
        families = {w["rank"]: w["family_id"] for w in self.store.list_words()}
        self.assertEqual(families, {1: 1, 2: 2, 3: None})
        entries = self.client.get("/api/words").json()["words"]
        self.assertEqual([e["ranks"] for e in entries], [[1], [2], [3]])


if __name__ == "__main__":
    unittest.main()
