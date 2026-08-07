"""
Testy panelu administracyjnego oraz wpiecia warstw w /validate.

Uzywamy TestClient (httpx). DNS omijamy, ograniczajac checks do ["syntax"] -
zgodnosc imie<->email i tak liczy sie dla adresu poprawnego skladniowo.
"""
from fastapi.testclient import TestClient

from app.main import app
from app import config, runtime, ai_client, smtp_check

client = TestClient(app)


# --- panel: odczyt / zapis przelacznikow ------------------------------------
def test_admin_get_returns_toggles():
    r = client.get("/admin/settings")
    assert r.status_code == 200
    body = r.json()
    assert set(body["toggles"].keys()) == {"ai", "crm", "smtp"}
    assert "integrations" in body


def test_admin_post_sets_toggle():
    r = client.post("/admin/settings", json={"crm": True})
    assert r.status_code == 200
    assert r.json()["toggles"]["crm"] is True
    assert runtime.enabled("crm") is True


def test_admin_post_subset_leaves_others():
    runtime.set_enabled("smtp", True)
    r = client.post("/admin/settings", json={"ai": True})
    tg = r.json()["toggles"]
    assert tg["ai"] is True
    assert tg["smtp"] is True   # nietkniete


def test_admin_token_required_when_set(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekret")
    assert client.get("/admin/settings").status_code == 401
    assert client.post("/admin/settings", json={"ai": True}).status_code == 401
    ok = client.get("/admin/settings", headers={"X-Admin-Token": "sekret"})
    assert ok.status_code == 200


def test_admin_panel_page_served():
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Panel konfiguracji" in r.text


def test_config_exposes_layers():
    body = client.get("/config").json()
    assert "toggles" in body
    assert "ai" in body and "smtp_check" in body and "crm" in body


# --- /validate: zgodnosc imie <-> email -------------------------------------
def test_validate_name_match_heuristic():
    r = client.post("/validate", json={
        "email": "kamil.fityka@mail.com",
        "name": "Kamil Ftyka",
        "checks": ["syntax"],
    })
    body = r.json()
    assert body["name_email_match"] == "partial"
    assert body["name_suggestion"] == "Kamil Fityka"
    assert body["name_match_source"] == "heuristic"


def test_validate_without_name_leaves_fields_null():
    body = client.post("/validate", json={"email": "kamil.fityka@wp.pl", "checks": ["syntax"]}).json()
    assert body["name_email_match"] is None
    assert body["name_suggestion"] is None


def test_validate_ai_overrides_heuristic(monkeypatch):
    runtime.set_enabled("ai", True)
    monkeypatch.setattr(config, "AI_BASE_URL", "http://llm.local/v1")
    monkeypatch.setattr(config, "AI_MODEL", "wlasny-model")
    monkeypatch.setattr(
        ai_client, "_call",
        lambda name, email: '{"status":"mismatch","suggestion":"","reason":"test"}',
    )
    body = client.post("/validate", json={
        "email": "kamil.fityka@wp.pl",
        "name": "Kamil Fityka",
        "checks": ["syntax"],
    }).json()
    assert body["name_email_match"] == "mismatch"
    assert body["name_match_source"] == "ai"


def test_validate_ai_disabled_stays_heuristic(monkeypatch):
    # Przelacznik ai wylaczony -> nie wolamy modelu, zostaje heurystyka.
    runtime.set_enabled("ai", False)
    monkeypatch.setattr(ai_client, "_call",
                        lambda name, email: (_ for _ in ()).throw(AssertionError("nie wolaj AI")))
    body = client.post("/validate", json={
        "email": "kamil.fityka@wp.pl", "name": "Kamil Fityka", "checks": ["syntax"],
    }).json()
    assert body["name_match_source"] == "heuristic"


# --- /validate: SMTP check ---------------------------------------------------
def test_validate_smtp_disabled_is_null():
    body = client.post("/validate", json={"email": "jan@wp.pl", "checks": ["syntax"]}).json()
    assert body["smtp_check"] is None


def test_validate_smtp_enabled_reports(monkeypatch):
    runtime.set_enabled("smtp", True)
    monkeypatch.setattr(smtp_check, "_resolve_mx", lambda d: ["mx.wp.pl"])

    class _S:
        def ehlo(self, h): return (250, b"")
        def helo(self, h): return (250, b"")
        def mail(self, s): return (250, b"")
        def rcpt(self, a): return (550, b"")   # odrzucenie -> undeliverable
        def quit(self): pass

    monkeypatch.setattr(smtp_check, "_connect", lambda host: _S())
    body = client.post("/validate", json={"email": "nieistnieje@wp.pl", "checks": ["syntax"]}).json()
    assert body["smtp_check"] == "undeliverable"
