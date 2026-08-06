"""
Testy L6 (double opt-in) — nowa architektura: trwaly store, mail multipart,
strona HTML potwierdzenia, /verify/status, limit wysylek.

Store ustawiamy na sqlite w tymczasowym pliku (przez env przed importem app),
zeby testy nie ruszaly produkcyjnej bazy i byly izolowane.
"""
import importlib
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    # osobna baza sqlite na kazdy test
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("VERIFY_STORE", "sqlite")
    monkeypatch.setenv("VERIFY_DB_PATH", tmp.name)
    monkeypatch.setenv("VERIFY_RATE_MAX", "3")
    monkeypatch.setenv("VERIFY_RATE_WINDOW_S", "3600")
    monkeypatch.setenv("SMTP_DRY_RUN", "true")

    # przeladuj moduly zaleznze od configu
    import app.config, app.verify_store, app.verify, app.main
    importlib.reload(app.config)
    importlib.reload(app.verify_store)
    importlib.reload(app.verify)
    importlib.reload(app.main)
    c = TestClient(app.main.app)
    yield c
    os.unlink(tmp.name)


def _token_for(email):
    # wyciagnij token bezposrednio ze store (dry-run nie wysyla maila)
    from app import verify_store
    import sqlite3
    con = sqlite3.connect(verify_store.store._path)
    row = con.execute("SELECT token FROM tokens WHERE email=? ORDER BY created_at DESC", (email,)).fetchone()
    con.close()
    return row[0] if row else None


def test_send_returns_sent(client):
    r = client.post("/verify/send", json={"email": "klient@example.com"})
    assert r.status_code == 200
    assert r.json()["status"] == "sent"


def test_send_rejects_bad_syntax(client):
    r = client.post("/verify/send", json={"email": "zla@@nazwa"})
    assert r.status_code == 422


def test_confirm_html_page(client):
    client.post("/verify/send", json={"email": "klient@example.com"})
    token = _token_for("klient@example.com")
    r = client.get(f"/verify/confirm?token={token}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "potwierdzony" in r.text.lower()


def test_confirm_json_contract(client):
    client.post("/verify/send", json={"email": "klient@example.com"})
    token = _token_for("klient@example.com")
    # kontrakt §6 nadal dostepny przez ?format=json
    r = client.get(f"/verify/confirm?token={token}&format=json")
    assert r.status_code == 200
    assert r.json() == {"email": "klient@example.com", "confirmed": True}


def test_confirm_token_is_one_time(client):
    client.post("/verify/send", json={"email": "klient@example.com"})
    token = _token_for("klient@example.com")
    r1 = client.get(f"/verify/confirm?token={token}&format=json")
    assert r1.status_code == 200
    r2 = client.get(f"/verify/confirm?token={token}&format=json")
    assert r2.status_code == 400  # zuzyty


def test_confirm_invalid_token_html(client):
    r = client.get("/verify/confirm?token=nieistniejacy")
    assert r.status_code == 400
    assert "text/html" in r.headers["content-type"]


def test_status_flow(client):
    email = "klient@example.com"
    r0 = client.get(f"/verify/status?email={email}")
    assert r0.json() == {"email": email, "confirmed": False, "pending": False}

    client.post("/verify/send", json={"email": email})
    r1 = client.get(f"/verify/status?email={email}")
    assert r1.json()["pending"] is True
    assert r1.json()["confirmed"] is False

    token = _token_for(email)
    client.get(f"/verify/confirm?token={token}&format=json")
    r2 = client.get(f"/verify/status?email={email}")
    assert r2.json()["confirmed"] is True
    assert r2.json()["pending"] is False


def test_already_confirmed_short_circuits(client):
    email = "klient@example.com"
    client.post("/verify/send", json={"email": email})
    token = _token_for(email)
    client.get(f"/verify/confirm?token={token}&format=json")
    # kolejny send nie wysyla ponownie
    r = client.post("/verify/send", json={"email": email})
    assert r.json()["status"] == "already_confirmed"


def test_rate_limit(client):
    email = "spam@example.com"
    for _ in range(3):
        assert client.post("/verify/send", json={"email": email}).status_code == 200
    r = client.post("/verify/send", json={"email": email})
    assert r.status_code == 429


def test_persistence_across_reload(client, monkeypatch):
    # token zapisany do sqlite przetrwa "restart" (reload modulu store)
    email = "klient@example.com"
    client.post("/verify/send", json={"email": email})
    token = _token_for(email)

    import importlib, app.verify_store, app.verify, app.main
    importlib.reload(app.verify_store)
    importlib.reload(app.verify)
    importlib.reload(app.main)
    from fastapi.testclient import TestClient
    c2 = TestClient(app.main.app)

    r = c2.get(f"/verify/confirm?token={token}&format=json")
    assert r.status_code == 200
    assert r.json()["confirmed"] is True
