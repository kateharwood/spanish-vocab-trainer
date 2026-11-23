import random
import tempfile
import unittest
from datetime import datetime, timezone

from app.definitions import DefinitionHelper, build_prompt
from app.store import SqliteStore
from app.study import StudyService


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


class RunNow:
    """Stands in for the thread pool so suggestions finish before asserting."""

    def submit(self, fn, *args):
        fn(*args)


class DefinitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SqliteStore(self.temp.name + "/test.db")
        self.store.upsert_words(
            [
                {"rank": 1, "spanish": "cansado", "english": "tired", "frequency": 9, "group_code": "2"},
                {"rank": 2, "spanish": "cansada", "english": "tired", "frequency": 8, "group_code": "2"},
                {"rank": 3, "spanish": "mesa", "english": "table", "frequency": 7, "group_code": "2"},
            ]
        )
        self.store.set_family([1, 2], 1)
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def helper(self, answer=None, error=None):
        def suggest(variants):
            self.calls.append([v["rank"] for v in variants])
            if error:
                raise error
            return answer(variants)

        return DefinitionHelper(self.store, suggest, executor=RunNow())

    def start(self):
        return StudyService(self.store, random.Random(1)).start_or_resume(NOW)

    def test_new_cards_get_one_request_per_family(self):
        def answer(variants):
            if variants[0]["rank"] == 3:
                return {"variants": [{"rank": 3, "english": "table", "alt_english": "", "changed": False}], "note": ""}
            return {
                "variants": [
                    {"rank": 1, "english": "tired (masc.)", "alt_english": "", "changed": True},
                    {"rank": 2, "english": "tired (fem.)", "alt_english": "", "changed": True},
                ],
                "note": "Marks which form is which.",
            }

        helper = self.helper(answer)
        session = self.start()
        self.assertEqual(helper.queue_for_session(session["session_id"]), 2)
        self.assertEqual(sorted(self.calls), [[1, 2], [3]])

        family = helper.for_ranks([1, 2])
        self.assertEqual(
            [s["suggested_english"] for s in family["suggestions"]],
            ["tired (masc.)", "tired (fem.)"],
        )
        # An unchanged definition is not offered.
        self.assertEqual(helper.for_ranks([3])["suggestions"], [])
        # Settled words are never requested again.
        self.assertEqual(helper.queue_for_session(session["session_id"]), 0)

    def test_accept_updates_words_and_dismiss_leaves_them(self):
        helper = self.helper(
            lambda variants: {
                "variants": [
                    {"rank": v["rank"], "english": v["english"] + " (better)",
                     "alt_english": "also " + v["english"], "changed": True}
                    for v in variants
                ],
                "note": "",
            }
        )
        helper.queue_for_session(self.start()["session_id"])

        accepted = helper.resolve([1, 2], accept=True)
        self.assertEqual({a["english"] for a in accepted}, {"tired (better)"})
        helper.resolve([3], accept=False)

        words = {w["rank"]: (w["english"], w["alt_english"]) for w in self.store.list_words()}
        self.assertEqual(
            words,
            {
                1: ("tired (better)", "also tired"),
                2: ("tired (better)", "also tired"),
                3: ("table", None),
            },
        )
        self.assertEqual(helper.for_ranks([1, 2, 3])["suggestions"], [])

    def test_decision_made_while_sorting_carries_into_study(self):
        helper = self.helper(
            lambda variants: {
                "variants": [
                    {"rank": v["rank"], "english": v["english"] + "!", "alt_english": "",
                     "changed": True}
                    for v in variants
                ],
                "note": "",
            }
        )
        # Sorting asks about one family ahead of any study session.
        self.assertEqual(helper.queue_families([[3]]), 1)
        helper.resolve([3], accept=False)
        self.calls.clear()

        helper.queue_for_session(self.start()["session_id"])
        self.assertEqual(self.calls, [[1, 2]])
        self.assertEqual(self.store.get_suggestions([3])[0]["status"], "dismissed")

    def test_main_definition_holds_one_meaning(self):
        helper = self.helper(
            lambda variants: {
                "variants": [
                    {"rank": 3, "english": "table; board", "alt_english": "panel",
                     "changed": True}
                ],
                "note": "",
            }
        )
        helper.queue_families([[3]])
        row = self.store.get_suggestions([3])[0]
        self.assertEqual((row["suggested_english"], row["suggested_alt"]),
                         ("table", "board; panel"))

    def test_accepting_an_old_suggestion_splits_it(self):
        self.store.upsert_suggestions([
            {"rank": 3, "original_english": "table", "suggested_english": "table; board",
             "note": "", "status": "pending", "updated_at": "2026-11-01T00:00:00+00:00"}
        ])
        helper = DefinitionHelper(self.store, None, executor=RunNow())
        self.assertEqual(helper.resolve([3], accept=True),
                         [{"rank": 3, "english": "table", "alt_english": "board"}])
        word = next(w for w in self.store.list_words() if w["rank"] == 3)
        self.assertEqual((word["english"], word["alt_english"]), ("table", "board"))

    def test_failure_is_retried_next_session(self):
        helper = self.helper(error=RuntimeError("boom"))
        session = self.start()
        helper.queue_for_session(session["session_id"])
        statuses = {row["status"] for row in self.store.get_suggestions([1, 2, 3])}
        self.assertEqual(statuses, {"failed"})
        self.assertEqual(helper.queue_for_session(session["session_id"]), 2)

    def test_disabled_without_a_suggester(self):
        helper = DefinitionHelper(self.store, None, executor=RunNow())
        self.assertFalse(helper.enabled)
        self.assertEqual(helper.queue_for_session(self.start()["session_id"]), 0)

    def test_prompt_mentions_family_only_for_families(self):
        single = build_prompt([{"rank": 3, "spanish": "mesa", "english": "table"}])
        family = build_prompt(
            [
                {"rank": 1, "spanish": "cansado", "english": "tired"},
                {"rank": 2, "spanish": "cansada", "english": "tired"},
            ]
        )
        self.assertNotIn("same word", single)
        self.assertIn("same word", family)
        self.assertIn(
            "rank 2 | spanish: cansada | current english: tired | current alt_english: ",
            family,
        )

    def test_on_vercel_waits_for_claude_and_shares_working_status(self):
        # On Vercel the request waits for Claude, and "working" lives in the
        # database so another server instance can see it.
        from concurrent.futures import ThreadPoolExecutor

        seen_working = []

        def suggest(variants):
            other = DefinitionHelper(self.store, lambda v: {}, executor=RunNow())
            seen_working.append(other.for_ranks([v["rank"] for v in variants])["working"])
            # Another instance must not ask again while this one is working.
            self.assertEqual(other.queue_families([[v["rank"] for v in variants]]), 0)
            return {"variants": [{"rank": 3, "english": "table, desk", "alt_english": "", "changed": True}]}

        helper = DefinitionHelper(
            self.store, suggest, executor=ThreadPoolExecutor(max_workers=2), wait=True
        )
        self.assertEqual(helper.queue_families([[3]]), 1)
        # Already done when queue_families returns.
        self.assertEqual(seen_working, [True])
        result = helper.for_ranks([3])
        self.assertFalse(result["working"])
        self.assertEqual(result["suggestions"][0]["suggested_english"], "table, desk")


if __name__ == "__main__":
    unittest.main()
