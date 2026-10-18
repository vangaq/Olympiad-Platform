"""Application factory and HTTP routes. No execution of submitted code."""
import os
import re
import sqlite3
import unicodedata
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import click
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from .db import cleanup_legacy_data, close_db, get_db, initialize_database, normalize_answer, record_attempt, username_key
from .formatting import datetime_ru, ru_plural, statement_text
from .security import check_csrf, csrf_token, login_required, persistent_secret, rate_limit

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_TASK_COLUMNS = "p.id, p.title, p.description, p.difficulty, p.category, p.input_data, p.answer_format"
CATEGORY_LABELS = {"math": "Математика", "arrays": "Массивы", "strings": "Строки", "general": "Общие задачи"}
DIFFICULTY_LABELS = {1: "Лёгкая", 2: "Средняя", 3: "Сложная"}


def category_label(category):
    return CATEGORY_LABELS.get(category, category)


def payload(allowed):
    if request.is_json:
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400, description="Ожидается JSON-объект.")
    else:
        if any(len(values) != 1 for _, values in request.form.lists()):
            abort(400, description="Поля не должны повторяться.")
        data = request.form.to_dict()
    if set(data) - set(allowed) - {"csrf_token"}:
        abort(400, description="Запрос содержит неподдерживаемые поля.")
    return data


def credentials(data, registration=False):
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("Введите логин и пароль.")
    username = unicodedata.normalize("NFKC", username).strip()
    if registration and not re.fullmatch(r"[\w.-]{3,32}", username):
        raise ValueError("Логин: 3–32 символа, буквы, цифры, точка, дефис или подчёркивание.")
    if not username or len(username) > 128:
        raise ValueError("Некорректный логин.")
    if len(password) > 128 or not password or (registration and (len(password) < 8 or not password.strip())):
        raise ValueError("Пароль должен содержать от 8 до 128 символов." if registration else "Некорректный пароль.")
    return username, password


def public_profile(account_id):
    db = get_db()
    user = db.execute("SELECT id, username FROM accounts WHERE id = ?", (account_id,)).fetchone()
    stats = db.execute(
        """SELECT (SELECT COUNT(*) FROM solves WHERE account_id = ?) AS solved_count,
                  (SELECT COUNT(*) FROM attempts WHERE account_id = ?) AS total_attempts,
                  (SELECT COUNT(*) FROM attempts WHERE account_id = ? AND correct = 1) AS correct_attempts""",
        (account_id, account_id, account_id),
    ).fetchone()
    solved = db.execute(
        """SELECT p.id, p.title, p.difficulty, p.active, s.earned_at
           FROM solves s JOIN problems p ON p.id = s.problem_id
           WHERE s.account_id = ? ORDER BY s.earned_at DESC, p.id DESC""", (account_id,),
    ).fetchall()
    return {"id": user["id"], "username": user["username"], "points": stats["solved_count"],
            **dict(stats), "solved_tasks": [dict(row) for row in solved]}


def history_data(account_id, page=1, problem_id=None, per_page=20):
    where, params = "a.account_id = ?", [account_id]
    if problem_id is not None:
        where += " AND a.problem_id = ?"
        params.append(problem_id)
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM attempts a WHERE {where}", params).fetchone()[0]
    rows = db.execute(
        f"""SELECT a.id, a.problem_id AS task_id, p.title AS task_title, p.difficulty, p.active,
                   a.answer, a.correct, a.kind, a.created_at
            FROM attempts a JOIN problems p ON p.id = a.problem_id
            WHERE {where} ORDER BY a.id DESC LIMIT ? OFFSET ?""",
        (*params, per_page, (page - 1) * per_page),
    ).fetchall()
    return {"attempts": [{**dict(row), "correct": bool(row["correct"]),
                         "result": "Верно" if row["correct"] else "Неверно"} for row in rows],
            "page": page, "pages": max(1, (total + per_page - 1) // per_page), "total": total}


def page_number():
    value = request.args.get("page", "1")
    if len(value) > 7 or not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 1000000:
        abort(400, description="Некорректный номер страницы.")
    return int(value)


def leaderboard_rows(limit=None):
    query = """SELECT a.id, a.username, COUNT(s.problem_id) AS points
               FROM accounts a LEFT JOIN solves s ON s.account_id = a.id
               GROUP BY a.id ORDER BY points DESC, a.username_key ASC, a.id ASC"""
    return get_db().execute(query + (" LIMIT ?" if limit else ""), (limit,) if limit else ()).fetchall()


def create_app(config=None):
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.jinja_env.filters.update(ru_plural=ru_plural, datetime_ru=datetime_ru, statement_text=statement_text)
    app.config.update(
        DATABASE=os.environ.get("DATABASE_PATH", str(ROOT / "database.db")),
        BACKUP_DIR=str(ROOT / "backups"), SECRET_KEY=os.environ.get("SECRET_KEY"),
        SECRET_FILE=str(ROOT / "instance" / "secret.key"),
        MAX_CONTENT_LENGTH=65536, MAX_FORM_MEMORY_SIZE=65536, MAX_FORM_PARTS=20,
        TEMPLATES_AUTO_RELOAD=True,
        SESSION_COOKIE_NAME="olympiad_session", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7), RATE_LIMIT_ENABLED=True,
    )
    if config:
        app.config.update(config)
    db_path = Path(app.config["DATABASE"])
    app.config["DATABASE"] = str(db_path if db_path.is_absolute() else ROOT / db_path)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = persistent_secret(app.config["SECRET_FILE"])
    if len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("SECRET_KEY must be at least 32 characters.")
    initialize_database(app)
    app.teardown_appcontext(close_db)

    @app.cli.command("cleanup-legacy")
    def cleanup_legacy():
        """Back up and remove imported code attempts, their points and old tables."""
        report = cleanup_legacy_data(app)
        click.echo(f"Резервная копия: {report['backup']}")
        click.echo(f"Удалено старых попыток: {report['removed_attempts']}")
        click.echo(f"Баллы пересчитаны по сохранённым ответам. Удалены таблицы: {', '.join(report['removed_tables']) or 'нет'}")

    @app.before_request
    def load_user_and_protect_requests():
        g.user = SimpleNamespace(is_authenticated=False, points=0)
        account_id = session.get("account_id")
        if isinstance(account_id, int):
            user = get_db().execute(
                """SELECT a.id, a.username, a.session_version, a.password_disabled,
                   (SELECT COUNT(*) FROM solves s WHERE s.account_id = a.id) AS points
                   FROM accounts a WHERE a.id = ?""", (account_id,),
            ).fetchone()
            if user and not user["password_disabled"] and user["session_version"] == session.get("session_version"):
                g.user = SimpleNamespace(**dict(user), is_authenticated=True)
            else:
                session.clear()
        if request.url_rule is None:
            return
        if (not g.user.is_authenticated and request.method == "GET"
                and not request.path.startswith("/api/")
                and request.endpoint not in {"login", "register", "static"}):
            return redirect(url_for("login"))
        if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request.is_json:
            abort(415, description="API принимает Content-Type: application/json.")
        check_csrf()

    @app.context_processor
    def template_context():
        return {"current_user": g.user, "csrf_token": csrf_token,
                "category_label": category_label, "difficulty_labels": DIFFICULTY_LABELS}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = error.get_response()
        messages = {404: "Страница или задача не найдена.", 405: "Метод запроса не поддерживается.",
                    413: "Запрос слишком большой.", 500: "Внутренняя ошибка. Попробуйте позже."}
        message = messages.get(error.code, error.description)
        if request.path.startswith("/api/"):
            response.data = app.json.dumps({"error": message})
            response.content_type = "application/json"
        else:
            response.data = render_template("error.html", status=error.code, message=message)
            response.content_type = "text/html; charset=utf-8"
        return response

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(error):
        app.logger.exception("Database operation failed")
        if request.path.startswith("/api/"):
            return jsonify(error="База временно недоступна. Повторите запрос."), 503
        return render_template("error.html", status=503, message="База временно недоступна. Повторите запрос."), 503

    @app.get("/")
    def index():
        db = get_db()
        stats = {"users": db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
                 "tasks": db.execute("SELECT COUNT(*) FROM problems WHERE active = 1").fetchone()[0],
                 "submissions": db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
                 "solved": db.execute("SELECT COUNT(*) FROM solves").fetchone()[0]}
        return render_template("index.html", stats=stats, top_users=leaderboard_rows(10))

    @app.get("/api/csrf")
    def api_csrf():
        return jsonify(csrf_token=csrf_token())

    @app.post("/api/register")
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            if g.user.is_authenticated:
                return redirect(url_for("index"))
            return render_template("register.html")
        rate_limit("register", request.remote_addr, 20, 3600)
        data = payload({"username", "password"})
        try:
            username, password = credentials(data, registration=True)
        except ValueError as error:
            if request.is_json:
                abort(400, description=str(error))
            return render_template("register.html", error=str(error)), 400
        try:
            account = get_db().execute("INSERT INTO accounts (username, username_key, password_hash) VALUES (?, ?, ?)",
                                       (username, username_key(username), generate_password_hash(password)))
        except sqlite3.IntegrityError:
            if request.is_json:
                abort(409, description="Этот логин уже занят.")
            return render_template("register.html", error="Этот логин уже занят."), 409
        if request.is_json:
            return jsonify(message="Аккаунт создан. Войдите в систему."), 201
        session.clear()
        session["account_id"] = account.lastrowid
        session["session_version"] = 0
        session.permanent = True
        csrf_token()
        flash("Аккаунт создан. Добро пожаловать!", "success")
        return redirect(url_for("index"), code=303)

    @app.post("/api/login")
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if g.user.is_authenticated:
                return redirect(url_for("index"))
            return render_template("login.html")
        rate_limit("login_ip", request.remote_addr, 50, 900)
        data = payload({"username", "password"})
        try:
            username, password = credentials(data)
        except ValueError as error:
            if request.is_json:
                abort(400, description=str(error))
            return render_template("login.html", error=str(error)), 400
        rate_limit("login_name", username_key(username), 15, 900)
        user = get_db().execute("SELECT * FROM accounts WHERE username_key = ?", (username_key(username),)).fetchone()
        valid = False
        if user and not user["password_disabled"]:
            try:
                valid = check_password_hash(user["password_hash"], password)
            except (ValueError, TypeError):
                pass
        if not valid:
            if request.is_json:
                abort(401, description="Неверный логин или пароль.")
            return render_template("login.html", error="Неверный логин или пароль."), 401
        session.clear()
        session["account_id"] = user["id"]
        session["session_version"] = user["session_version"]
        session.permanent = True
        token = csrf_token()
        if request.is_json:
            return jsonify(user=public_profile(user["id"]), csrf_token=token)
        flash(f"Добро пожаловать, {user['username']}!", "success")
        return redirect(url_for("index"), code=303)

    @app.post("/api/logout")
    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        if request.is_json:
            return jsonify(message="Вы вышли из аккаунта.", csrf_token=csrf_token())
        flash("Вы вышли из аккаунта.", "info")
        return redirect(url_for("login"), code=303)

    @app.get("/api/tasks")
    @app.get("/tasks")
    def tasks():
        difficulty, category = request.args.get("difficulty", "all"), request.args.get("category", "all")
        if difficulty not in {"all", "1", "2", "3"} or len(category) > 50:
            abort(400, description="Некорректный фильтр задач.")
        where, params = "p.active = 1", []
        if difficulty != "all":
            where += " AND p.difficulty = ?"
            params.append(int(difficulty))
        if category != "all":
            where += " AND p.category = ?"
            params.append(category)
        rows = get_db().execute(f"SELECT {PUBLIC_TASK_COLUMNS} FROM problems p WHERE {where} ORDER BY p.difficulty, p.id", params).fetchall()
        progress = {row[0]: True for row in get_db().execute("SELECT problem_id FROM solves WHERE account_id = ?", (g.user.id,))} if g.user.is_authenticated else {}
        if request.path.startswith("/api/"):
            return jsonify(tasks=[{**dict(row), "category_name": category_label(row["category"]),
                                   "solved": row["id"] in progress} for row in rows])
        categories = get_db().execute("SELECT DISTINCT category FROM problems WHERE active = 1 ORDER BY category").fetchall()
        return render_template("tasks.html", tasks=rows, categories=categories, user_progress=progress, difficulty=difficulty, category=category)

    @app.post("/api/tasks/<int:task_id>/submit")
    @app.get("/api/tasks/<int:task_id>")
    @app.route("/task/<int:task_id>", methods=["GET", "POST"])
    @login_required
    def task(task_id):
        task_row = get_db().execute(f"SELECT {PUBLIC_TASK_COLUMNS} FROM problems p WHERE p.id = ? AND p.active = 1", (task_id,)).fetchone()
        if task_row is None:
            abort(404)
        if request.method == "POST":
            data = payload({"answer"})
            answer = data.get("answer")
            if not isinstance(answer, str) or not 1 <= len(answer) <= 2000 or not normalize_answer(answer):
                abort(400, description="Введите ответ длиной от 1 до 2000 символов.")
            rate_limit("submit", g.user.id, 120, 60)
            try:
                result = record_attempt(g.user.id, task_id, answer)
            except LookupError:
                abort(404)
            if request.path.startswith("/api/"):
                return jsonify(result), 201
            if result["correct"]:
                flash("Верно! +1 балл." if result["points_awarded"] else "Верно! Эта задача уже решена, балл был начислен ранее.", "success")
            else:
                flash("Неверно. Попробуйте ещё раз.", "error")
            return redirect(url_for("task", task_id=task_id), code=303)
        solved = bool(get_db().execute("SELECT 1 FROM solves WHERE account_id = ? AND problem_id = ?", (g.user.id, task_id)).fetchone())
        if request.path.startswith("/api/"):
            return jsonify(task={**dict(task_row), "category_name": category_label(task_row["category"]), "solved": solved})
        return render_template("task.html", task=task_row, solved=solved,
                               submissions=history_data(g.user.id, problem_id=task_id, per_page=10)["attempts"])

    @app.get("/api/me")
    @app.get("/profile")
    @login_required
    def profile():
        info = public_profile(g.user.id)
        if request.path.startswith("/api/"):
            return jsonify(info)
        return render_template("profile.html", profile=info, submissions=history_data(g.user.id)["attempts"])

    @app.get("/api/history")
    @app.get("/history")
    @login_required
    def history():
        data = history_data(g.user.id, page_number())
        if request.path.startswith("/api/"):
            return jsonify(data)
        return render_template("history.html", **data)

    @app.get("/api/leaderboard")
    @app.get("/leaderboard")
    def leaderboard():
        rows = leaderboard_rows()
        if request.path.startswith("/api/"):
            return jsonify(users=[dict(row) for row in rows])
        return render_template("leaderboard.html", users=rows)

    @app.cli.command("reset-password")
    @click.argument("username")
    @click.password_option(confirmation_prompt=True)
    def reset_password(username, password):
        """Set a new password locally and invalidate previous sessions."""
        try:
            _, password = credentials({"username": "password_reset", "password": password}, registration=True)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        changed = get_db().execute(
            """UPDATE accounts SET password_hash = ?, password_disabled = 0, session_version = session_version + 1
               WHERE username_key = ?""", (generate_password_hash(password), username_key(username.strip())),
        ).rowcount
        if not changed:
            raise click.ClickException("Пользователь не найден.")
        click.echo("Пароль обновлён. Предыдущие сессии отозваны.")

    return app
