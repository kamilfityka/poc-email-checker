"""Testy opcjonalnego klienta AI (app/ai_client.py) - bez realnej sieci."""
from app import ai_client, config, runtime


def _enable(monkeypatch):
    runtime.set_enabled("ai", True)
    monkeypatch.setattr(config, "AI_BASE_URL", "http://llm.local/v1")
    monkeypatch.setattr(config, "AI_MODEL", "wlasny")


def test_disabled_returns_none():
    runtime.set_enabled("ai", False)
    assert ai_client.refine_name_match("Jan Kowalski", "jan@wp.pl", None) is None


def test_enabled_but_unconfigured_is_disabled(monkeypatch):
    runtime.set_enabled("ai", True)
    monkeypatch.setattr(config, "AI_BASE_URL", "")
    monkeypatch.setattr(config, "AI_MODEL", "")
    assert ai_client.is_enabled() is False


def test_valid_json_verdict(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ai_client, "_call",
                        lambda n, e: 'tekst {"status":"match","suggestion":"","reason":"ok"} ogon')
    r = ai_client.refine_name_match("Jan Kowalski", "jan.kowalski@wp.pl", None)
    assert r == {"status": "match", "suggestion": None, "source": "ai"}


def test_bad_json_degrades_to_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ai_client, "_call", lambda n, e: "to nie jest json")
    assert ai_client.refine_name_match("Jan", "jan@wp.pl", None) is None


def test_call_exception_degrades_to_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ai_client, "_call",
                        lambda n, e: (_ for _ in ()).throw(RuntimeError("timeout")))
    assert ai_client.refine_name_match("Jan", "jan@wp.pl", None) is None


def test_invalid_status_rejected(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ai_client, "_call", lambda n, e: '{"status":"cos-dziwnego"}')
    assert ai_client.refine_name_match("Jan", "jan@wp.pl", None) is None
