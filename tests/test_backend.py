import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

from flask import Flask
from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

from olympiad import create_app
from olympiad.db import connect_database

TEST_KEY = "testing-only-secret-do-not-use-in-production-0123456789"
PASSWORD = "test-password-2026"


def process_submit(config, cookie, token):
    app = create_app(config)
    with app.test_client() as client:
        client.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie)
        result = client.post("/api/tasks/1/submit", json={"answer": "95"}, headers={"X-CSRF-Token": token})
        return result.status_code, result.get_json()


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {"TESTING": True, "DATABASE": str(self.root / "test.db"),
                       "BACKUP_DIR": str(self.root / "backups"), "SECRET_KEY": TEST_KEY,
                       "RATE_LIMIT_ENABLED": False}
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def sql(self, statement, params=()):
        db = connect_database(self.config["DATABASE"])
        try:
            return [dict(row) for row in db.execute(statement, params).fetchall()]
        finally:
            db.close()

    def token(self, client=None):
        return (client or self.client).get("/api/csrf").get_json()["csrf_token"]

    def post(self, path, data, client=None, token=None):
        client = client or self.client
        return client.post(path, json=data, headers={"X-CSRF-Token": token or self.token(client)})

    def register(self, username="alice", client=None):
        return self.post("/api/register", {"username": username, "password": PASSWORD}, client)

    def login(self, username="alice", client=None):
        return self.post("/api/login", {"username": username, "password": PASSWORD}, client)

    def account(self, username="alice", client=None):
        self.assertEqual(self.register(username, client).status_code, 201)
        response = self.login(username, client)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_fresh_start_and_html_routes(self):
        catalog = self.client.get("/api/tasks").get_json()["tasks"]
        self.assertGreaterEqual(len(catalog), 5)
        self.assertEqual({row["difficulty"] for row in catalog}, {1, 2, 3})
        for path in ("/", "/tasks", "/login", "/register", "/leaderboard", "/static/app.js"):
            with self.subTest(path=path):
                with self.client.get(path) as response:
                    self.assertEqual(response.status_code, 200)
        self.account()
        for path in ("/task/1", "/profile", "/history"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        html = self.client.get("/task/1").get_data(as_text=True)
        self.assertIn('action="/task/1"', html)
        self.assertIn('action="/logout"', html)
        self.assertNotIn("socket.io", html)

    def test_registration_login_errors_and_case_insensitive_uniqueness(self):
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register("ALICE").status_code, 409)
        self.assertEqual(self.post("/api/login", {"username": "alice", "password": "wrong"}).status_code, 401)
        self.assertEqual(self.login("ALICE").status_code, 200)
        stored = self.sql("SELECT password_hash FROM accounts")[0]["password_hash"]
        self.assertNotEqual(stored, PASSWORD)
        for data in ({}, {"username": "x", "password": PASSWORD},
                     {"username": "short", "password": "123"},
                     {"username": [], "password": PASSWORD},
                     {"username": "space user", "password": PASSWORD},
                     {"username": "extra", "password": PASSWORD, "is_admin": True}):
            with self.subTest(data=data):
                self.assertEqual(self.post("/api/register", data).status_code, 400)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM accounts")[0]["n"], 1)

    def test_two_accounts_scoring_history_and_leaderboard(self):
        alice = self.account()
        bob_client = self.app.test_client()
        bob = self.account("bob", bob_client)
        wrong = self.post("/api/tasks/1/submit", {"answer": "94"}).get_json()
        right = self.post("/api/tasks/1/submit", {"answer": "95"}).get_json()
        again = self.post("/api/tasks/1/submit", {"answer": "  95\n"}).get_json()
        self.assertEqual((wrong["result"], wrong["points_awarded"]), ("Неверно", 0))
        self.assertEqual((right["result"], right["points_awarded"], right["points"]), ("Верно", 1, 1))
        self.assertEqual((again["points_awarded"], again["points"]), (0, 1))
        self.post("/api/tasks/2/submit", {"answer": "84"})
        self.post("/api/tasks/1/submit", {"answer": "95"}, bob_client)
        profile = self.client.get("/api/me").get_json()
        self.assertEqual((profile["solved_count"], profile["points"], profile["total_attempts"]), (2, 2, 4))
        history = self.client.get("/api/history").get_json()
        self.assertEqual(history["total"], 4)
        self.assertEqual(history["attempts"][-1]["answer"], "94")
        self.assertFalse(history["attempts"][-1]["correct"])
        self.assertEqual(bob_client.get("/api/history").get_json()["total"], 1)
        rows = self.client.get("/api/leaderboard").get_json()["users"]
        self.assertEqual([(row["username"], row["points"]) for row in rows], [("alice", 2), ("bob", 1)])
        for response in (bob_client.get(f"/api/me?user_id={alice['user']['id']}"),
                         self.client.get(f"/api/me?user_id={bob['user']['id']}")):
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f"/api/history?account_id={bob['user']['id']}").get_json()["total"], 4)
        self.assertEqual(bob_client.get("/api/tasks/2").get_json()["task"]["solved"], False)

    def test_concurrent_correct_requests_award_once_and_keep_all_attempts(self):
        self.account()
        token = self.token()
        cookie = self.client.get_cookie(self.app.config["SESSION_COOKIE_NAME"]).value
        barrier = threading.Barrier(16)
        def submit(_):
            client = self.app.test_client()
            client.set_cookie(self.app.config["SESSION_COOKIE_NAME"], cookie)
            barrier.wait(timeout=15)
            result = client.post("/api/tasks/1/submit", json={"answer": "95"}, headers={"X-CSRF-Token": token})
            return result.status_code, result.get_json()
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(submit, range(16)))
        self.assertTrue(all(code == 201 for code, _ in results), results)
        self.assertEqual(sum(result["points_awarded"] for _, result in results), 1)
        self.assertEqual(self.client.get("/api/me").get_json()["points"], 1)
        self.assertEqual(self.client.get("/api/history").get_json()["total"], 16)

    def test_scoring_across_independent_processes(self):
        self.account()
        token = self.token()
        cookie = self.client.get_cookie(self.app.config["SESSION_COOKIE_NAME"]).value
        with ProcessPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(process_submit, self.config, cookie, token) for _ in range(6)]
            results = [future.result(timeout=45) for future in futures]
        self.assertTrue(all(code == 201 for code, _ in results), results)
        self.assertEqual(sum(result["points_awarded"] for _, result in results), 1)
        self.assertEqual(self.client.get("/api/history").get_json()["total"], 6)

    def test_private_routes_and_removed_admin(self):
        for path in ("/api/me", "/api/history", "/api/tasks/1"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.post("/api/tasks/1/submit", {"answer": "95"}).status_code, 401)
        self.account()
        self.assertEqual(self.client.get("/admin").status_code, 404)
        self.assertEqual(self.client.post("/admin/delete_task/1", data={"csrf_token": self.token()}).status_code, 404)
        for path in ("/pvp", "/socket.io/"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_score_and_identity_tampering_is_rejected(self):
        self.account()
        for extra in ({"points": 1000}, {"user_id": 999}, {"account_id": 999},
                      {"solved": True}, {"correct": True}, {"is_admin": True}, {"task_id": 2}, {"code": "print(95)"}):
            result = self.post("/api/tasks/1/submit", {"answer": "95", **extra})
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.post("/api/me", {"points": 999}).status_code, 405)
        self.assertEqual(self.client.get("/api/me").get_json()["points"], 0)
        self.assertEqual(self.client.get("/api/history").get_json()["total"], 0)

    def test_csrf_required_and_rotated_on_login(self):
        self.assertEqual(self.client.post("/api/register", json={"username": "alice", "password": PASSWORD}).status_code, 400)
        self.register()
        old_token = self.token()
        self.login()
        self.assertEqual(self.post("/api/tasks/1/submit", {"answer": "95"}, token=old_token).status_code, 400)
        other = self.app.test_client()
        self.assertEqual(self.post("/api/tasks/1/submit", {"answer": "95"}, token=self.token(other)).status_code, 400)
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.post("/api/logout", {}).status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_invalid_payloads_and_bounds(self):
        self.account()
        for data in ({}, [], None, {"answer": None}, {"answer": 95}, {"answer": []},
                     {"answer": {}}, {"answer": True}, {"answer": ""}, {"answer": "  \n"}, {"answer": "a" * 2001}):
            response = self.client.post("/api/tasks/1/submit", data=json.dumps(data), content_type="application/json", headers={"X-CSRF-Token": self.token()})
            self.assertEqual(response.status_code, 400, data)
        self.assertEqual(self.client.post("/api/tasks/1/submit", data="{", content_type="application/json", headers={"X-CSRF-Token": self.token()}).status_code, 400)
        self.assertEqual(self.client.post("/api/tasks/1/submit", data={"answer": "95", "csrf_token": self.token()}).status_code, 415)
        self.assertEqual(self.post("/api/tasks/1/submit", {"answer": "x" * 70000}).status_code, 413)
        duplicated = MultiDict([("csrf_token", self.token()), ("answer", "95"), ("answer", "0")])
        self.assertEqual(self.client.post("/task/1", data=duplicated).status_code, 400)
        for path in ("/api/tasks?difficulty=oops", "/api/history?page=0", "/api/history?page=nope"):
            self.assertEqual(self.client.get(path).status_code, 400)
        self.assertEqual(self.client.get("/api/history?page=" + "9" * 5000).status_code, 400)
        self.assertEqual(self.post("/api/tasks/999999/submit", {"answer": "95"}).status_code, 404)
        self.assertEqual(self.client.get("/api/history").get_json()["total"], 0)

    def test_answers_never_leak_in_task_payloads_or_html(self):
        self.account()
        sentinel = "PRIVATE-EXPECTED-ANSWER-73910"
        self.sql("UPDATE problems SET correct_answer = ? WHERE id = 1", (sentinel,))
        for path in ("/api/tasks", "/api/tasks/1", "/tasks", "/task/1", "/", "/api/me"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(sentinel, response.get_data(as_text=True))
            self.assertNotIn('"correct_answer"', response.get_data(as_text=True))
        response = self.post("/api/tasks/1/submit", {"answer": "wrong"})
        self.assertNotIn(sentinel, response.get_data(as_text=True))

    def test_xss_is_escaped_and_code_is_just_an_answer(self):
        self.account()
        malicious = '<img src=x onerror="alert(1)">'
        self.post("/api/tasks/1/submit", {"answer": malicious})
        for path in ("/history", "/task/1", "/profile"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertNotIn(malicious, html)
            self.assertIn("&lt;img", html)
        self.sql("UPDATE problems SET description = ? WHERE id = 1", ("<script>alert(1)</script>",))
        self.assertIn("&lt;script&gt;", self.client.get("/task/1").get_data(as_text=True))
        marker = self.root / "must-not-be-created"
        code = f"__import__('pathlib').Path({str(marker)!r}).write_text('bad')"
        result = self.post("/api/tasks/1/submit", {"answer": code}).get_json()
        self.assertFalse(result["correct"])
        self.assertFalse(marker.exists())
        response = self.client.get("/profile")
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_persistence_and_session_key_survive_app_restart(self):
        config = {**self.config, "SECRET_KEY": None, "SECRET_FILE": str(self.root / "secret.key")}
        first = create_app(config)
        client = first.test_client()
        self.account(client=client)
        self.post("/api/tasks/1/submit", {"answer": "95"}, client)
        self.post("/api/tasks/2/submit", {"answer": "wrong"}, client)
        cookie = client.get_cookie(first.config["SESSION_COOKIE_NAME"]).value
        second = create_app(config)
        self.assertEqual(first.secret_key, second.secret_key)
        reopened = second.test_client()
        reopened.set_cookie(second.config["SESSION_COOKIE_NAME"], cookie)
        self.assertEqual(reopened.get("/api/me").get_json()["points"], 1)
        self.assertEqual(reopened.get("/api/history").get_json()["total"], 2)
        self.post("/api/logout", {}, reopened)
        self.assertEqual(self.login(client=reopened).status_code, 200)
        self.assertEqual(reopened.get("/api/me").get_json()["points"], 1)

    def test_old_public_secret_cannot_forge_session(self):
        user = self.account()["user"]
        old = Flask("old")
        old.secret_key = "olymp-platform-secret-key-2024"
        forged = old.session_interface.get_signing_serializer(old).dumps({"account_id": user["id"], "session_version": 0})
        outsider = self.app.test_client()
        outsider.set_cookie(self.app.config["SESSION_COOKIE_NAME"], forged)
        self.assertEqual(outsider.get("/api/me").status_code, 401)

    def test_history_pagination_and_attempts_on_unsolved_tasks(self):
        self.account()
        for i in range(23):
            self.post("/api/tasks/1/submit", {"answer": f"wrong-{i}"})
        first = self.client.get("/api/history").get_json()
        second = self.client.get("/api/history?page=2").get_json()
        self.assertEqual((first["total"], first["pages"], len(first["attempts"]), len(second["attempts"])), (23, 2, 20, 3))
        self.assertEqual(self.client.get("/api/me").get_json()["total_attempts"], 23)
        self.assertEqual(self.client.get("/api/me").get_json()["points"], 0)

    def test_archive_preserves_history_and_points(self):
        self.account()
        self.post("/api/tasks/1/submit", {"answer": "95"})
        self.sql("UPDATE problems SET active = 0 WHERE id = 1")
        self.assertEqual(self.client.get("/api/tasks/1").status_code, 404)
        self.assertEqual(self.post("/api/tasks/1/submit", {"answer": "95"}).status_code, 404)
        self.assertEqual(self.client.get("/api/me").get_json()["points"], 1)
        self.assertEqual(self.client.get("/api/history").get_json()["total"], 1)
        self.assertEqual(self.sql("PRAGMA foreign_key_check"), [])

    def test_no_administrative_routes_roles_or_creation_command(self):
        self.account()
        for path in ("/admin", "/admin/add_task", "/admin/delete_task/1"):
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.post(path, data={"csrf_token": self.token()}).status_code, 404)
        self.assertNotIn("create-admin", self.app.cli.commands)
        self.assertNotIn("is_admin", {row["name"] for row in self.sql("PRAGMA table_info(accounts)")})
        self.assertNotIn("Админка", self.client.get("/profile").get_data(as_text=True))
        self.assertNotIn('href="/admin"', self.client.get("/profile").get_data(as_text=True))

    def test_version_two_removes_default_admin_and_preserves_other_users(self):
        self.account()
        self.post("/api/tasks/1/submit", {"answer": "95"})
        self.post("/api/tasks/2/submit", {"answer": "wrong"})
        self.sql("ALTER TABLE accounts ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1))")
        self.sql("INSERT INTO accounts (id, username, username_key, password_hash, is_admin) VALUES (20, 'admin', 'admin', ?, 1)",
                 (generate_password_hash(PASSWORD),))
        self.sql("INSERT INTO attempts (account_id, problem_id, answer, correct) VALUES (20, 1, '95', 1)")
        self.sql("INSERT INTO solves (account_id, problem_id) VALUES (20, 1)")
        self.sql("DELETE FROM schema_migrations WHERE version = 2")
        previous_session = self.app.test_client()
        with previous_session.session_transaction() as session:
            session["account_id"], session["session_version"] = 20, 0
        cookie = previous_session.get_cookie(self.app.config["SESSION_COOKIE_NAME"]).value
        self.app = create_app(self.config)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM accounts")[0]["n"], 1)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM attempts")[0]["n"], 2)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM solves")[0]["n"], 1)
        self.assertNotIn("is_admin", {row["name"] for row in self.sql("PRAGMA table_info(accounts)")})
        self.assertEqual(self.sql("PRAGMA foreign_key_check"), [])
        client = self.app.test_client()
        client.set_cookie(self.app.config["SESSION_COOKIE_NAME"], cookie)
        self.assertEqual(client.get("/api/me").status_code, 401)
        self.assertEqual(self.login(client=client).get_json()["user"]["points"], 1)
        self.assertEqual([row["username"] for row in client.get("/api/leaderboard").get_json()["users"]], ["alice"])
        backups = list((self.root / "backups").glob("before-remove-admin-*.db"))
        self.assertEqual(len(backups), 1)
        backup = sqlite3.connect(backups[0])
        self.assertEqual(backup.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 2)
        backup.close()
        create_app(self.config)
        self.assertEqual(len(list((self.root / "backups").glob("before-remove-admin-*.db"))), 1)

    def test_transaction_rolls_back_attempt_when_award_fails(self):
        self.account()
        self.sql("CREATE TRIGGER fail_award BEFORE INSERT ON solves BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.post("/api/tasks/1/submit", {"answer": "95"})
        self.assertEqual(self.client.get("/api/history").get_json()["total"], 0)
        self.assertEqual(self.client.get("/api/me").get_json()["points"], 0)

    def test_password_reset_revokes_existing_sessions(self):
        self.account()
        result = self.app.test_cli_runner().invoke(args=["reset-password", "alice"], input="replacement-password\nreplacement-password\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.post("/api/login", {"username": "alice", "password": "replacement-password"}).status_code, 200)

    def test_rate_limit_persists_across_restart(self):
        self.register()
        self.app.config["RATE_LIMIT_ENABLED"] = True
        for _ in range(15):
            self.assertEqual(self.post("/api/login", {"username": "alice", "password": "wrong"}).status_code, 401)
        restarted = create_app({**self.config, "RATE_LIMIT_ENABLED": True})
        blocked = self.post("/api/login", {"username": "alice", "password": "wrong"}, restarted.test_client())
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_html_form_registration_submission_and_logout(self):
        def form_post(path, fields):
            return self.client.post(path, data={**fields, "csrf_token": self.token()}, follow_redirects=True)
        self.assertEqual(form_post("/register", {"username": "forms", "password": PASSWORD}).status_code, 200)
        response = form_post("/login", {"username": "forms", "password": PASSWORD})
        self.assertEqual(response.request.path, "/tasks")
        response = form_post("/task/1", {"answer": "95"})
        self.assertIn("Верно! +1 балл.", response.get_data(as_text=True))
        self.assertEqual(response.request.path, "/task/1")
        self.assertEqual(form_post("/logout", {}).request.path, "/")


class LegacyMigrationTest(unittest.TestCase):
    def test_preserves_accounts_history_and_unique_solves_without_default_admin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT, password_hash TEXT, is_admin INTEGER, rating INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, description TEXT, difficulty INTEGER, category TEXT, sample_input TEXT, output_format TEXT, test_cases TEXT);
                CREATE TABLE submissions (id INTEGER PRIMARY KEY, user_id INTEGER, task_id INTEGER, code TEXT, status TEXT, submitted_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE user_progress (user_id INTEGER, task_id INTEGER, solved INTEGER, first_solved_at TEXT);
            """)
            for uid, name, password, admin in ((1, "admin", "admin123", 1), (2, "legacy", PASSWORD, 0)):
                db.execute("INSERT INTO users (id, username, password_hash, is_admin, rating) VALUES (?, ?, ?, ?, 9999)", (uid, name, generate_password_hash(password), admin))
            db.execute("INSERT INTO tasks VALUES (1, 'Sum', 'Add', 1, 'math', '3 5', 'Integer', ?)", (json.dumps([{"input": "3 5", "output": "8"}, {"input": "10 20", "output": "30"}]),))
            for sid, status in ((1, "accepted"), (2, "accepted"), (3, "wrong_answer")):
                db.execute("INSERT INTO submissions (id, user_id, task_id, code, status) VALUES (?, 2, 1, 'print(30)', ?)", (sid, status))
            db.execute("INSERT INTO user_progress VALUES (2, 1, 1, CURRENT_TIMESTAMP)")
            db.commit(); db.close()
            config = {"TESTING": True, "DATABASE": str(path), "SECRET_KEY": TEST_KEY, "BACKUP_DIR": str(Path(directory) / "backups"), "RATE_LIMIT_ENABLED": False}
            app = create_app(config)
            client = app.test_client()
            def login(name, password):
                token = client.get("/api/csrf").get_json()["csrf_token"]
                return client.post("/api/login", json={"username": name, "password": password}, headers={"X-CSRF-Token": token})
            self.assertEqual(login("admin", "admin123").status_code, 401)
            self.assertEqual([row["username"] for row in client.get("/api/leaderboard").get_json()["users"]], ["legacy"])
            self.assertEqual(login("legacy", PASSWORD).status_code, 200)
            profile = client.get("/api/me").get_json()
            self.assertEqual((profile["points"], profile["total_attempts"]), (1, 3))
            self.assertEqual(client.get("/api/history").get_json()["attempts"][0]["kind"], "legacy_code")
            self.assertEqual(client.get("/api/tasks/1").get_json()["task"]["input_data"], "10 20")
            create_app(config)
            self.assertEqual(len(list((Path(directory) / "backups").glob("*.db"))), 1)
            db = sqlite3.connect(path)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM solves").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            db.close()


if __name__ == "__main__":
    unittest.main()
