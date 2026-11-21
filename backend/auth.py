"""Inloggning: ett enda konto, ingen registrering.

Användarnamn och lösenord läses från miljövariablerna AUTH_USERNAME och
AUTH_PASSWORD (aldrig från koden; repot kan vara publikt). Är AUTH_PASSWORD
inte satt är inloggningen avstängd, vilket bara är tänkt för lokal utveckling.

Sessionen är en HMAC-signerad cookie (HttpOnly, SameSite=Lax, Secure bakom
https) som gäller i 12 timmar. Hemligheten tas från SESSION_SECRET, annars
slumpas den när servern startar (då loggas alla ut vid omstart).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from pydantic import BaseModel

COOKIE = "receipts_session"
SESSION_SECONDS = 12 * 3600
MAX_ATTEMPTS = 8  # misslyckade inloggningar per IP-adress ...
ATTEMPT_WINDOW = 300  # ... under fem minuter

_SECRET = (os.getenv("SESSION_SECRET") or secrets.token_hex(32)).encode()
_failed: dict[str, deque[float]] = defaultdict(deque)


class User(BaseModel):
    id: str
    name: str
    role: str
    authenticated: bool


DEMO_USER = User(id="demo", name="Demo clinician", role="clinician", authenticated=False)


def auth_enabled() -> bool:
    return bool(os.getenv("AUTH_PASSWORD"))


def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def make_token(username: str) -> str:
    payload = f"{username}|{int(time.time()) + SESSION_SECONDS}"
    return f"{payload}|{_sign(payload)}"


def read_token(token: str | None) -> str | None:
    """Användarnamnet om cookien är äkta och inte har gått ut, annars None."""
    if not token or token.count("|") != 2:
        return None
    username, expires, signature = token.split("|")
    if not hmac.compare_digest(signature, _sign(f"{username}|{expires}")):
        return None
    try:
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    return username


def check_credentials(username: str, password: str) -> bool:
    expected_user = os.getenv("AUTH_USERNAME", "Demo")
    expected_password = os.getenv("AUTH_PASSWORD", "")
    user_ok = hmac.compare_digest(username.strip().encode(), expected_user.encode())
    password_ok = hmac.compare_digest(password.encode(), expected_password.encode())
    return bool(expected_password) and user_ok and password_ok


def too_many_attempts(client: str) -> bool:
    now = time.time()
    attempts = _failed[client]
    while attempts and attempts[0] < now - ATTEMPT_WINDOW:
        attempts.popleft()
    return len(attempts) >= MAX_ATTEMPTS


def record_failure(client: str) -> None:
    _failed[client].append(time.time())


def reset_failures(client: str) -> None:
    _failed.pop(client, None)


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def get_current_user(request: Request) -> User:
    if not auth_enabled():
        return DEMO_USER
    username = read_token(request.cookies.get(COOKIE))
    if username is None:
        raise HTTPException(401, "Sign in to continue.")
    return User(id=username.lower(), name=username, role="clinician", authenticated=True)
