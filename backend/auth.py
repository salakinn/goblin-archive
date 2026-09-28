"""Single-administrator authentication, stored separately from the book database."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

COOKIE = "goblin_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = 15 * 60


def _connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "auth.db"
    connection = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    connection.execute("CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK (id = 1), salt BLOB NOT NULL, password_hash BLOB NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash BLOB PRIMARY KEY, expires INTEGER NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS auth_attempts (client TEXT NOT NULL, attempted_at INTEGER NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_auth_attempts_client ON auth_attempts (client, attempted_at)")
    connection.commit()
    return connection


def _setup_path(data_dir: Path) -> Path:
    return data_dir / "auth-setup-code"


def initialized(data_dir: Path) -> bool:
    with _connect(data_dir) as db:
        return db.execute("SELECT 1 FROM admin WHERE id = 1").fetchone() is not None


def ensure_setup_code(data_dir: Path) -> None:
    if initialized(data_dir):
        _setup_path(data_dir).unlink(missing_ok=True)
        return
    path = _setup_path(data_dir)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="ascii") as file:
        file.write(secrets.token_urlsafe(32) + "\n")


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**15, r=8, p=1,
                          maxmem=64 * 1024 * 1024, dklen=32)


def _throttle(data_dir: Path, key: str) -> None:
    now = int(time.time())
    with _connect(data_dir) as db:
        db.execute("DELETE FROM auth_attempts WHERE attempted_at < ?", (now - ATTEMPT_WINDOW,))
        count = db.execute("SELECT count(*) FROM auth_attempts WHERE client = ?", (key,)).fetchone()[0]
        if count >= MAX_ATTEMPTS:
            raise HTTPException(429, detail={"message": "Zu viele Versuche. Bitte in 15 Minuten erneut versuchen."})
        db.execute("INSERT INTO auth_attempts VALUES (?, ?)", (key, now))
        db.commit()


def _clear_attempts(data_dir: Path, key: str) -> None:
    with _connect(data_dir) as db:
        db.execute("DELETE FROM auth_attempts WHERE client = ?", (key,))
        db.commit()


def setup(data_dir: Path, code: str, password: str, client: str) -> str:
    key = f"setup:{client}"
    _throttle(data_dir, key)
    if len(password) < 12 or len(password) > 1024:
        raise HTTPException(400, detail={"message": "Das Passwort muss 12 bis 1024 Zeichen lang sein."})
    ensure_setup_code(data_dir)
    expected = _setup_path(data_dir).read_text(encoding="ascii").strip()
    if not hmac.compare_digest(code, expected):
        raise HTTPException(401, detail={"message": "Setup-Code ungültig."})
    salt = secrets.token_bytes(32)
    with _connect(data_dir) as db:
        inserted = db.execute("INSERT OR IGNORE INTO admin (id, salt, password_hash) VALUES (1, ?, ?)",
                              (salt, _hash_password(password, salt)))
        if not inserted.rowcount:
            raise HTTPException(409, detail={"message": "Anmeldung ist bereits eingerichtet."})
        db.commit()
    _setup_path(data_dir).unlink(missing_ok=True)
    _clear_attempts(data_dir, key)
    return create_session(data_dir)


def login(data_dir: Path, password: str, client: str) -> str:
    key = f"login:{client}"
    _throttle(data_dir, key)
    with _connect(data_dir) as db:
        row = db.execute("SELECT salt, password_hash FROM admin WHERE id = 1").fetchone()
    if not row or not hmac.compare_digest(_hash_password(password, row[0]), row[1]):
        raise HTTPException(401, detail={"message": "Passwort ungültig."})
    _clear_attempts(data_dir, key)
    return create_session(data_dir)


def create_session(data_dir: Path) -> str:
    token = secrets.token_urlsafe(32)
    with _connect(data_dir) as db:
        db.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
        db.execute("INSERT INTO sessions VALUES (?, ?)",
                   (hashlib.sha256(token.encode()).digest(), int(time.time()) + SESSION_SECONDS))
        db.commit()
    return token


def valid_session(data_dir: Path, token: str | None) -> bool:
    if not token:
        return False
    with _connect(data_dir) as db:
        row = db.execute("SELECT expires FROM sessions WHERE token_hash = ?",
                         (hashlib.sha256(token.encode()).digest(),)).fetchone()
    return bool(row and row[0] > time.time())


def logout(data_dir: Path, token: str | None) -> None:
    if not token:
        return
    with _connect(data_dir) as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?",
                   (hashlib.sha256(token.encode()).digest(),))
        db.commit()


def require_request_auth(request: Request, data_dir: Path) -> None:
    if not valid_session(data_dir, request.cookies.get(COOKIE)):
        raise HTTPException(401, detail={"message": "Bitte anmelden."})


def check_origin(request: Request) -> None:
    """Reject browser cross-origin writes, including login and setup."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin and request.headers.get("sec-fetch-site") != "same-origin" and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, detail={"message": "Ungültige Herkunft der Anfrage."})
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, detail={"message": "Ungültige Herkunft der Anfrage."})


def check_csrf(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    check_origin(request)
    token = request.cookies.get(COOKIE)
    if not token or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf_token(token)):
        raise HTTPException(403, detail={"message": "CSRF-Prüfung fehlgeschlagen."})


def csrf_token(token: str) -> str:
    return hashlib.sha256(b"goblin-csrf:" + token.encode()).hexdigest()


def reset_password(data_dir: Path, password: str) -> None:
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("Das Passwort muss 12 bis 1024 Zeichen lang sein.")
    salt = secrets.token_bytes(32)
    with _connect(data_dir) as db:
        db.execute("INSERT INTO admin (id, salt, password_hash) VALUES (1, ?, ?) "
                   "ON CONFLICT(id) DO UPDATE SET salt=excluded.salt, password_hash=excluded.password_hash",
                   (salt, _hash_password(password, salt)))
        db.execute("DELETE FROM sessions")
        db.commit()
    _setup_path(data_dir).unlink(missing_ok=True)


if __name__ == "__main__":
    import getpass
    import sys
    from backend.config import Settings

    if sys.argv[1:] != ["reset-password"]:
        raise SystemExit("Verwendung: python -m backend.auth reset-password")
    first = getpass.getpass("Neues Admin-Passwort: ")
    second = getpass.getpass("Passwort wiederholen: ")
    if first != second:
        raise SystemExit("Passwörter stimmen nicht überein.")
    try:
        reset_password(Settings().data_dir.resolve(), first)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("Admin-Passwort geändert; alle Sitzungen wurden beendet.")
