"""Inloggningsväggen: ett konto, ingen registrering, allt annat stängt."""
import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.app import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_USERNAME", "Demo")
    monkeypatch.setenv("AUTH_PASSWORD", "Demo123")
    auth._failed.clear()
    with TestClient(app, follow_redirects=False) as c:
        yield c
    auth._failed.clear()


def sign_in(c, username="Demo", password="Demo123"):
    return c.post("/api/login", json={"username": username, "password": password})


def test_everything_is_closed_without_signing_in(client):
    assert client.get("/app/").status_code == 303
    assert client.get("/app/").headers["location"] == "/login?next=/app/"
    assert client.get("/").status_code == 303
    assert client.get("/app.js").status_code == 303
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/audit", json={}).status_code == 401
    assert client.get("/api/docs").status_code == 401


def test_sign_in_page_and_health_are_open(client):
    assert client.get("/login").status_code == 200
    assert client.get("/style.css").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_wrong_credentials_are_rejected(client):
    assert sign_in(client, password="wrong").status_code == 401
    assert sign_in(client, username="Admin").status_code == 401
    assert sign_in(client, password="demo123").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_sign_in_opens_the_app_and_sign_out_closes_it(client):
    r = sign_in(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    me = client.get("/api/me").json()
    assert me["authenticated"] is True and me["name"] == "Demo"
    assert client.get("/app/").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_tampered_or_expired_cookie_is_rejected(client):
    token = auth.make_token("Demo")
    user, expires, signature = token.split("|")
    client.cookies.set(auth.COOKIE, f"Admin|{expires}|{signature}")
    assert client.get("/api/me").status_code == 401
    client.cookies.set(auth.COOKIE, f"{user}|1|{auth._sign(f'{user}|1')}")
    assert client.get("/api/me").status_code == 401


def test_there_is_no_sign_up(client):
    for path in ("/api/signup", "/api/register", "/api/users"):
        sign_in(client)
        assert client.post(path, json={"username": "x", "password": "y"}).status_code in (404, 405)


def test_repeated_failures_are_slowed_down(client):
    for _ in range(auth.MAX_ATTEMPTS):
        sign_in(client, password="nope")
    assert sign_in(client).status_code == 429
