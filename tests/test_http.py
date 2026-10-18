"""Acceptance test against the actual Waitress server, including restart."""
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class HttpAcceptanceTest(unittest.TestCase):
    def test_real_server_two_users_concurrency_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            env = {**os.environ, "DATABASE_PATH": str(root / "acceptance.db"),
                   "SECRET_KEY": "http-test-only-stable-secret-01234567890123456789",
                   "HOST": "127.0.0.1", "PORT": str(port), "COOKIE_SECURE": "0"}
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py")]
            process = None
            log = (root / "server.log").open("w+", encoding="utf-8")

            def start():
                nonlocal process
                # A different CWD verifies that paths do not depend on the shell.
                process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=log)
                for _ in range(100):
                    if process.poll() is not None:
                        log.seek(0)
                        self.fail(log.read())
                    try:
                        with urllib.request.urlopen(base + "/api/tasks", timeout=1) as response:
                            if response.status == 200:
                                return
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.05)
                self.fail("Server did not become ready")

            def stop():
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)

            def browser():
                jar = http.cookiejar.CookieJar()
                return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar

            def get(client, path):
                with client.open(base + path, timeout=15) as response:
                    return json.load(response)

            def post(client, path, data):
                token = get(client, "/api/csrf")["csrf_token"]
                request = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                                 headers={"Content-Type": "application/json", "X-CSRF-Token": token})
                with client.open(request, timeout=15) as response:
                    return json.load(response)

            try:
                start()
                alice, jar = browser()
                bob, _ = browser()
                for client, name in ((alice, "http_alice"), (bob, "http_bob")):
                    post(client, "/api/register", {"username": name, "password": "http-test-password"})
                    post(client, "/api/login", {"username": name, "password": "http-test-password"})
                self.assertEqual(post(alice, "/api/tasks/1/submit", {"answer": "wrong"})["result"], "Неверно")
                token = get(alice, "/api/csrf")["csrf_token"]
                cookies = "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar)
                def concurrent_submit(_):
                    request = urllib.request.Request(base + "/api/tasks/1/submit", data=b'{"answer":"95"}',
                        headers={"Content-Type": "application/json", "X-CSRF-Token": token, "Cookie": cookies})
                    with urllib.request.urlopen(request, timeout=30) as response:
                        return json.load(response)
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(concurrent_submit, range(12)))
                self.assertEqual(sum(result["points_awarded"] for result in results), 1)
                self.assertEqual(get(alice, "/api/history")["total"], 13)
                self.assertEqual(get(bob, "/api/history")["total"], 0)
                self.assertEqual(get(bob, "/api/me")["points"], 0)
                rows = get(alice, "/api/leaderboard")["users"]
                self.assertEqual([(row["username"], row["points"]) for row in rows], [("http_alice", 1), ("http_bob", 0)])
                stop()
                start()
                self.assertEqual(get(alice, "/api/me")["points"], 1)
                self.assertEqual(get(alice, "/api/history")["total"], 13)
                self.assertEqual(post(alice, "/api/tasks/1/submit", {"answer": "95"})["points_awarded"], 0)
                post(alice, "/api/logout", {})
                post(alice, "/api/login", {"username": "http_alice", "password": "http-test-password"})
                self.assertEqual(get(alice, "/api/history")["total"], 14)
            finally:
                stop()
                log.close()


if __name__ == "__main__":
    unittest.main()
