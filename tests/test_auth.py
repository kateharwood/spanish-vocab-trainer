import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import auth
from app.store import SqliteStore


class AuthTests(unittest.TestCase):
    def setUp(self):
        import app.main as main

        self.temp = tempfile.TemporaryDirectory()
        self.originals = main.store
        main.store = SqliteStore(self.temp.name + "/test.db")
        self.main = main

    def tearDown(self):
        self.main.store = self.originals
        self.temp.cleanup()

    def client(self):
        return TestClient(self.main.app, follow_redirects=False)

    def test_no_password_means_no_login_when_run_locally(self):
        with mock.patch.dict(os.environ, {"APP_PASSWORD": "", "VERCEL": ""}):
            self.assertEqual(self.client().get("/api/words").status_code, 200)

    def test_vercel_refuses_to_run_without_a_password(self):
        with mock.patch.dict(os.environ, {"APP_PASSWORD": "", "VERCEL": "1"}):
            self.assertEqual(self.client().get("/").status_code, 503)

    def test_everything_needs_signing_in(self):
        with mock.patch.dict(os.environ, {"APP_PASSWORD": "pan-con-tomate"}):
            client = self.client()
            self.assertEqual(client.get("/api/words").status_code, 401)
            page = client.get("/")
            self.assertEqual((page.status_code, page.headers["location"]), (303, "/login"))
            self.assertEqual(client.get("/static/app.js").status_code, 303)
            self.assertIn("Password", client.get("/login").text)

    def test_wrong_password_then_right_password(self):
        with mock.patch.dict(os.environ, {"APP_PASSWORD": "pan-con-tomate"}), \
                mock.patch("app.auth.asyncio.sleep", new=mock.AsyncMock()):
            client = self.client()
            wrong = client.post("/login", data={"password": "nope"})
            self.assertIn("not the password", wrong.text)
            self.assertNotIn(auth.COOKIE, client.cookies)
            right = client.post("/login", data={"password": "pan-con-tomate"})
            self.assertEqual(right.status_code, 303)
            self.assertEqual(client.get("/api/words").status_code, 200)

    def test_changing_the_password_signs_everyone_out(self):
        token = auth.make_token("old")
        self.assertTrue(auth.token_is_valid(token, "old"))
        self.assertFalse(auth.token_is_valid(token, "new"))
        self.assertFalse(auth.token_is_valid(token + "x", "old"))
        self.assertFalse(auth.token_is_valid(auth.make_token("old", now=0), "old"))


if __name__ == "__main__":
    unittest.main()
