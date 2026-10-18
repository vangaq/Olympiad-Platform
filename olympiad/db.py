"""SQLite storage, atomic scoring and a non-destructive legacy migration."""
import json
import secrets
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g
from .seeds import PROBLEMS

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        password_disabled INTEGER NOT NULL DEFAULT 0 CHECK (password_disabled IN (0, 1)),
        session_version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS problems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, description TEXT NOT NULL,
        difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 3),
        category TEXT NOT NULL DEFAULT 'math', input_data TEXT NOT NULL DEFAULT '',
        answer_format TEXT NOT NULL DEFAULT 'Введите ответ.', correct_answer TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        problem_id INTEGER NOT NULL REFERENCES problems(id), answer TEXT NOT NULL,
        correct INTEGER NOT NULL CHECK (correct IN (0, 1)),
        kind TEXT NOT NULL DEFAULT 'answer' CHECK (kind IN ('answer', 'legacy_code')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS solves (
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        problem_id INTEGER NOT NULL REFERENCES problems(id),
        earned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (account_id, problem_id))""",
    "CREATE INDEX IF NOT EXISTS attempts_account_id ON attempts(account_id, id DESC)",
    """CREATE TABLE IF NOT EXISTS rate_limits (
        bucket TEXT NOT NULL, identity TEXT NOT NULL,
        window_start INTEGER NOT NULL, hits INTEGER NOT NULL,
        PRIMARY KEY (bucket, identity))""",
]


def connect_database(path):
    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def get_db():
    if "db" not in g:
        g.db = connect_database(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def normalize_answer(value):
    # No Python, expression or SQL evaluation; only text normalization.
    return " ".join(unicodedata.normalize("NFKC", value).split())


def username_key(username):
    return unicodedata.normalize("NFKC", username).casefold()


def _migrate_legacy(connection, tables):
    """Keep old tables intact; copy supported data to the new schema."""
    account_ids, problem_ids = set(), set()
    for user in connection.execute("SELECT * FROM users ORDER BY id").fetchall():
        name = unicodedata.normalize("NFKC", user["username"]).strip()
        if username_key(name) == "admin" and user["is_admin"]:
            continue
        if connection.execute("SELECT 1 FROM accounts WHERE username_key = ?", (username_key(name),)).fetchone():
            name = f"{name[:24]}_{user['id']}"
            while connection.execute("SELECT 1 FROM accounts WHERE username_key = ?", (username_key(name),)).fetchone():
                name = f"user_{user['id']}_{secrets.token_hex(3)}"
        connection.execute(
            """INSERT INTO accounts
               (id, username, username_key, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user["id"], name, username_key(name), user["password_hash"], user["created_at"]),
        )
        account_ids.add(user["id"])
    if "tasks" in tables:
        for task in connection.execute("SELECT * FROM tasks ORDER BY id").fetchall():
            try:
                cases = json.loads(task["test_cases"] or "[]")
                valid = [case for case in cases if isinstance(case, dict)
                         and isinstance(case.get("input"), str)
                         and isinstance(case.get("output"), str)
                         and normalize_answer(case["output"])]
                if not valid:
                    continue
                case = next((c for c in valid if c["input"] != task["sample_input"]), valid[0])
            except (ValueError, TypeError):
                continue
            description = task["description"] + "\n\nДля приведённых входных данных запишите результат. Программу отправлять не нужно."
            connection.execute(
                """INSERT INTO problems
                   (id, title, description, difficulty, category, input_data, answer_format, correct_answer)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (task["id"], task["title"], description, min(3, max(1, task["difficulty"] or 1)),
                 task["category"] or "general", case["input"], task["output_format"] or "Введите ответ.", case["output"]),
            )
            problem_ids.add(task["id"])
    if "submissions" in tables:
        for attempt in connection.execute("SELECT * FROM submissions ORDER BY id").fetchall():
            if attempt["user_id"] not in account_ids or attempt["task_id"] not in problem_ids:
                continue
            correct = attempt["status"] == "accepted"
            connection.execute(
                """INSERT INTO attempts (id, account_id, problem_id, answer, correct, kind, created_at)
                   VALUES (?, ?, ?, ?, ?, 'legacy_code', ?)""",
                (attempt["id"], attempt["user_id"], attempt["task_id"], attempt["code"], correct, attempt["submitted_at"]),
            )
            if correct:
                connection.execute(
                    "INSERT OR IGNORE INTO solves (account_id, problem_id, earned_at) VALUES (?, ?, ?)",
                    (attempt["user_id"], attempt["task_id"], attempt["submitted_at"]),
                )
    if "user_progress" in tables:
        for progress in connection.execute("SELECT * FROM user_progress WHERE solved = 1").fetchall():
            if progress["user_id"] in account_ids and progress["task_id"] in problem_ids:
                connection.execute(
                    """INSERT OR IGNORE INTO solves (account_id, problem_id, earned_at)
                       VALUES (?, ?, COALESCE(?, CURRENT_TIMESTAMP))""",
                    (progress["user_id"], progress["task_id"], progress["first_solved_at"]),
                )


def initialize_database(app):
    path = Path(app.config["DATABASE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_database(path)
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        legacy_import = "users" in tables and "schema_migrations" not in tables
        remove_roles = "accounts" in tables and "is_admin" in {
            row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
        }
        if legacy_import or remove_roles:
            backup_dir = Path(app.config["BACKUP_DIR"])
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            label = "legacy" if legacy_import else "before-remove-admin"
            backup = backup_dir / f"{label}-{stamp}-{secrets.token_hex(3)}.db"
            with closing(sqlite3.connect(backup)) as destination:
                connection.backup(destination)
            app.logger.warning("Database migration backup: %s", backup)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA:
            connection.execute(statement)
        if not connection.execute("SELECT 1 FROM schema_migrations WHERE version = 1").fetchone():
            if "users" in tables:
                _migrate_legacy(connection, tables)
            count = connection.execute("SELECT COUNT(*) FROM problems WHERE active = 1").fetchone()[0]
            if count < 5:
                connection.executemany(
                    """INSERT INTO problems
                       (title, description, difficulty, category, input_data, answer_format, correct_answer)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""", PROBLEMS,
                )
            connection.execute("INSERT INTO schema_migrations (version) VALUES (1)")
        if not connection.execute("SELECT 1 FROM schema_migrations WHERE version = 2").fetchone():
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(accounts)")}
            if "is_admin" in columns:
                default_ids = [row[0] for row in connection.execute(
                    "SELECT id FROM accounts WHERE username_key = 'admin' AND is_admin = 1"
                )]
                for account_id in default_ids:
                    connection.execute("DELETE FROM attempts WHERE account_id = ?", (account_id,))
                    connection.execute("DELETE FROM solves WHERE account_id = ?", (account_id,))
                    connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
                # Other existing accounts keep their history but no longer have roles.
                connection.execute("ALTER TABLE accounts DROP COLUMN is_admin")
            connection.execute("INSERT INTO schema_migrations (version) VALUES (2)")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def cleanup_legacy_data(app):
    """Explicit maintenance: preserve modern answers and accounts, back up first."""
    backup_dir = Path(app.config["BACKUP_DIR"])
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"before-cleanup-legacy-{stamp}-{secrets.token_hex(3)}.db"
    connection = connect_database(app.config["DATABASE"])
    try:
        with closing(sqlite3.connect(backup)) as destination:
            connection.backup(destination)
        connection.execute("BEGIN IMMEDIATE")
        removed_attempts = connection.execute("DELETE FROM attempts WHERE kind = 'legacy_code'").rowcount
        # Rebuild points only from correct answers that remain in the working history.
        connection.execute("DELETE FROM solves")
        connection.execute(
            """INSERT INTO solves (account_id, problem_id, earned_at)
               SELECT account_id, problem_id, MIN(created_at)
               FROM attempts WHERE correct = 1 AND kind = 'answer'
               GROUP BY account_id, problem_id"""
        )
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        removed_tables = []
        # These are unused tables of the old code-submission/PvP application.
        for table in ("submissions", "user_progress", "pvp_matches", "tasks", "users"):
            if table in tables:
                connection.execute(f'DROP TABLE "{table}"')
                removed_tables.append(table)
        connection.commit()
        return {"backup": str(backup), "removed_attempts": removed_attempts,
                "removed_tables": removed_tables}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_attempt(account_id, problem_id, answer):
    """Save the attempt and award once in one transaction across all workers.

    BEGIN IMMEDIATE serializes writers; the composite primary key prevents
    duplicate solves. Points are COUNT(solves), not a mutable client counter.
    """
    connection = get_db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        task = connection.execute(
            "SELECT correct_answer FROM problems WHERE id = ? AND active = 1", (problem_id,)
        ).fetchone()
        if task is None:
            raise LookupError("Задача не найдена")
        correct = normalize_answer(answer) == normalize_answer(task["correct_answer"])
        attempt = connection.execute(
            "INSERT INTO attempts (account_id, problem_id, answer, correct) VALUES (?, ?, ?, ?)",
            (account_id, problem_id, answer, correct),
        )
        awarded = 0
        if correct:
            awarded = connection.execute(
                "INSERT INTO solves (account_id, problem_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (account_id, problem_id),
            ).rowcount
        points = connection.execute("SELECT COUNT(*) FROM solves WHERE account_id = ?", (account_id,)).fetchone()[0]
        connection.commit()
        return {"attempt_id": attempt.lastrowid, "correct": correct,
                "result": "Верно" if correct else "Неверно", "points_awarded": awarded, "points": points}
    except Exception:
        connection.rollback()
        raise
