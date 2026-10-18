"""Session, CSRF and persistent request throttling helpers."""
import hashlib
import hmac
import os
import secrets
import time
from functools import wraps
from pathlib import Path

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.exceptions import TooManyRequests
from .db import get_db


def persistent_secret(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another worker may have just created the file and be writing it.
        for _ in range(40):
            value = path.read_text(encoding="ascii").strip()
            if len(value) >= 64:
                return value
            time.sleep(0.025)
        raise RuntimeError("Invalid session secret file; restore it or set SECRET_KEY.")
    else:
        value = secrets.token_hex(32)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        return value


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def check_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    supplied = request.headers.get("X-CSRF-Token") if request.is_json else request.form.get("csrf_token")
    expected = session.get("csrf_token")
    if (not isinstance(supplied, str) or not isinstance(expected, str)
            or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))):
        abort(400, description="Срок действия формы истёк. Обновите страницу и попробуйте ещё раз.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user.is_authenticated:
            if request.path.startswith("/api/"):
                abort(401, description="Войдите в аккаунт.")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def rate_limit(bucket, identity, limit, seconds):
    if not current_app.config["RATE_LIMIT_ENABLED"]:
        return
    identity = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
    now = int(time.time())
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DELETE FROM rate_limits WHERE window_start < ?", (now - 86400,))
        row = db.execute("SELECT * FROM rate_limits WHERE bucket = ? AND identity = ?", (bucket, identity)).fetchone()
        if row and row["window_start"] + seconds > now:
            if row["hits"] >= limit:
                db.rollback()
                raise TooManyRequests("Слишком много запросов. Попробуйте позже.", retry_after=row["window_start"] + seconds - now)
            db.execute("UPDATE rate_limits SET hits = hits + 1 WHERE bucket = ? AND identity = ?", (bucket, identity))
        else:
            db.execute(
                """INSERT INTO rate_limits (bucket, identity, window_start, hits) VALUES (?, ?, ?, 1)
                   ON CONFLICT(bucket, identity) DO UPDATE SET window_start = excluded.window_start, hits = 1""",
                (bucket, identity, now),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
