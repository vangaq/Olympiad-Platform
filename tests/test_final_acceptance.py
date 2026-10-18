"""Exercise the complete bundled catalog on an isolated database."""
import tempfile
import unittest
from pathlib import Path

from olympiad import create_app


class CompleteCatalogAcceptanceTest(unittest.TestCase):
    def test_all_23_tasks_wrong_correct_repeat_and_two_users(self):
        expected_answers = (
            "30", "odd", "1", "yes", "10", "5", "cba", "no", "1", "42", "5",
            "-1", "0", "yes", "1", "abc", "1 2 3", "1", "16 16", "no", "5", "ABC", "0",
        )
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "DATABASE": str(Path(directory) / "catalog.db"),
                "SECRET_KEY": "catalog-acceptance-test-key-not-for-production-0123456789",
                "RATE_LIMIT_ENABLED": False,
            })
            alice, bob = app.test_client(), app.test_client()

            def post(client, path, data):
                token = client.get("/api/csrf").get_json()["csrf_token"]
                return client.post(path, json=data, headers={"X-CSRF-Token": token})

            for client, username in ((alice, "catalog_alice"), (bob, "catalog_bob")):
                credentials = {"username": username, "password": "temporary-test-password"}
                self.assertEqual(post(client, "/api/register", credentials).status_code, 201)
                self.assertEqual(post(client, "/api/login", credentials).status_code, 200)

            tasks = alice.get("/api/tasks").get_json()["tasks"]
            self.assertEqual(len(tasks), 23)
            self.assertEqual({task["difficulty"] for task in tasks}, {1, 2, 3})
            self.assertEqual({task["category"] for task in tasks}, {"math", "strings", "arrays"})
            for task_id, answer in enumerate(expected_answers, 1):
                with self.subTest(task_id=task_id):
                    task = alice.get(f"/api/tasks/{task_id}").get_json()["task"]
                    self.assertTrue(task["title"] and task["description"] and task["answer_format"])
                    self.assertNotIn("correct_answer", task)
                    path = f"/api/tasks/{task_id}/submit"
                    wrong = post(alice, path, {"answer": "incorrect-answer-for-audit"}).get_json()
                    self.assertEqual((wrong["correct"], wrong["points_awarded"]), (False, 0))
                    right = post(alice, path, {"answer": answer}).get_json()
                    self.assertEqual((right["correct"], right["points_awarded"], right["points"]), (True, 1, task_id))
                    repeated = post(alice, path, {"answer": answer}).get_json()
                    self.assertEqual((repeated["correct"], repeated["points_awarded"], repeated["points"]), (True, 0, task_id))

            profile = alice.get("/api/me").get_json()
            self.assertEqual((profile["points"], profile["solved_count"], profile["total_attempts"]), (23, 23, 69))
            self.assertEqual(len(profile["solved_tasks"]), 23)
            self.assertEqual(alice.get("/api/history").get_json()["total"], 69)
            self.assertEqual(bob.get("/api/history").get_json()["total"], 0)
            self.assertEqual(bob.get("/api/me").get_json()["points"], 0)
            self.assertEqual(post(bob, "/api/tasks/1/submit", {"answer": "30"}).get_json()["points"], 1)
            ranking = alice.get("/api/leaderboard").get_json()["users"]
            self.assertEqual([(row["username"], row["points"]) for row in ranking], [("catalog_alice", 23), ("catalog_bob", 1)])
