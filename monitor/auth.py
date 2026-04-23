"""
Authentication: bcrypt password hashing + itsdangerous signed cookie.
Users stored in users.json alongside this file.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Optional

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from fastapi import Request

USERS_FILE = Path(__file__).parent / "users.json"
SESSION_COOKIE = "cam_session"
SESSION_MAX_AGE = 86400  # 24 hours


# ─── user store ──────────────────────────────────────────────────────────────

def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_users(users: dict) -> None:
    USERS_FILE.write_text(
        json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def ensure_default_admin() -> None:
    """Create admin/admin with must_change_password=True if no users exist."""
    users = _load_users()
    if "admin" not in users:
        pw_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode()
        users["admin"] = {
            "password_hash": pw_hash,
            "must_change_password": True,
        }
        _save_users(users)


# ─── password ops ────────────────────────────────────────────────────────────

def verify_password(username: str, password: str) -> Optional[dict]:
    """Return user dict if credentials valid, else None."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return None
    if bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return user
    return None


def change_password(username: str, new_password: str) -> None:
    users = _load_users()
    if username not in users:
        raise ValueError("user not found")
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    users[username]["password_hash"] = pw_hash
    users[username]["must_change_password"] = False
    _save_users(users)


# ─── session cookie ──────────────────────────────────────────────────────────

def make_session_cookie(secret_key: str, username: str) -> str:
    signer = TimestampSigner(secret_key)
    return signer.sign(username).decode()


def verify_session_cookie(secret_key: str, cookie: str) -> Optional[str]:
    signer = TimestampSigner(secret_key)
    try:
        return signer.unsign(cookie, max_age=SESSION_MAX_AGE).decode()
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request, secret_key: str) -> Optional[dict]:
    """Return user dict (with 'username' injected) or None."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    username = verify_session_cookie(secret_key, cookie)
    if not username:
        return None
    users = _load_users()
    user = users.get(username)
    if not user:
        return None
    return {"username": username, **user}
