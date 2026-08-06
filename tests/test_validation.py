"""
Testy jednostkowe + kontraktowe. DNS jest mockowany, zeby testy byly szybkie
i niezalezne od sieci. Sprawdzamy kluczowe kryteria sukcesu (§14):
  - literowki wykrywane z sugestia,
  - NXDOMAIN vs timeout rozroznione (not_found vs unknown),
  - unknown NIGDY nie blokuje zapisu,
  - syntax_invalid twardo blokuje.
"""
import pytest
from fastapi.testclient import TestClient

from app import validation, config
from app.main import app

client = TestClient(app)


# --- L0 skladnia -------------------------------------------------------------
@pytest.mark.parametrize("email,ok", [
    ("jan.kowalski@wp.pl", True),
    ("a@b.co", True),
    ("bez-malpy.pl", False),
    ("dwa@@at.com", False),
    ("spacja @wp.pl", False),
    ("brak@domeny", False),
    ("@wp.pl", False),
    ("kropka.@wp.pl", False),
])
def test_syntax(email, ok):
    valid, _, _ = validation.check_syntax(email)
    assert valid is ok


# --- L1 literowki ------------------------------------------------------------
@pytest.mark.parametrize("domain,expected", [
    ("gmial.com", "gmail.com"),
    ("gamil.com", "gmail.com"),
    ("interia.pll", "interia.pl"),
    ("wp.p", "wp.pl"),
    ("gmail.com", None),      # poprawna -> brak sugestii
    ("moja-firma.pl", None),  # nietypowa, daleko od slownika -> brak sugestii
])
def test_typo(domain, expected):
    assert validation.suggest_domain(domain) == expected


# --- L4 listy ----------------------------------------------------------------
def test_role_based():
    assert validation.is_role_based("biuro") is True
    assert validation.is_role_based("jan.kowalski") is False


def test_disposable_list_loaded():
    assert len(validation.DISPOSABLE_DOMAINS) > 1000  # snapshot sie zaladowal


# --- Orkiestracja z mockiem DNS ---------------------------------------------
def _mock_dns(monkeypatch, status, has_mx=True, has_a=True):
    monkeypatch.setattr(
        validation, "check_dns_mx",
        lambda domain: {"domain_status": status, "has_mx": has_mx,
                        "has_a": has_a, "cached": False},
    )


def test_syntax_invalid_blocks(monkeypatch):
    out = validation.validate("zla@@nazwa", ["syntax", "dns"])
    assert out["result"] == "syntax_invalid"
    assert config.resolve_block(out["result"])["block_save"] is True


def test_typo_short_circuits_dns(monkeypatch):
    # nawet gdyby DNS byl wolany, typo ma pierwszenstwo
    out = validation.validate("jan@gmial.com", ["syntax", "typo", "dns", "mx"])
    assert out["result"] == "typo_suspected"
    assert out["suggestion"] == "jan@gmail.com"
    assert out["domain_status"] == "not_checked"


def test_domain_not_found(monkeypatch):
    _mock_dns(monkeypatch, "not_found", has_mx=False, has_a=False)
    out = validation.validate("jan@nieistnieje-xyz.pl", ["syntax", "dns", "mx"])
    assert out["result"] == "domain_not_found"


def test_dns_timeout_is_unknown_and_never_blocks(monkeypatch):
    _mock_dns(monkeypatch, "unknown", has_mx=None, has_a=None)
    out = validation.validate("jan@moja-firma-xyz.pl", ["syntax", "dns", "mx"])
    assert out["result"] == "unknown"
    # KRYTERIUM: unknown nigdy nie blokuje
    assert config.resolve_block(out["result"])["block_save"] is False


def test_no_mail_capability(monkeypatch):
    _mock_dns(monkeypatch, "ok", has_mx=False, has_a=False)
    out = validation.validate("jan@example-nomail.pl", ["syntax", "dns", "mx"])
    assert out["result"] == "no_mail_capability"


def test_mx_fallback_to_a_is_valid(monkeypatch):
    _mock_dns(monkeypatch, "ok", has_mx=False, has_a=True)
    out = validation.validate("jan@only-a-record.pl", ["syntax", "dns", "mx", "lists"])
    assert out["result"] == "valid"


def test_valid(monkeypatch):
    _mock_dns(monkeypatch, "ok", has_mx=True, has_a=True)
    out = validation.validate("jan.kowalski@moja-firma-abc.pl", ["syntax", "dns", "mx", "lists"])
    assert out["result"] == "valid"


# --- Kontrakt API (§6) -------------------------------------------------------
def test_validate_endpoint_contract(monkeypatch):
    _mock_dns(monkeypatch, "ok", has_mx=True, has_a=True)
    r = client.post("/validate", json={"email": "jan@moja-firma-abc.pl",
                                       "checks": ["syntax", "dns", "mx", "lists"]})
    assert r.status_code == 200
    data = r.json()
    for field in ["email", "result", "block_save", "syntax_valid", "domain_status",
                  "has_mx", "disposable", "role_based", "suggestion", "message_pl",
                  "cached", "elapsed_ms"]:
        assert field in data, f"brak pola {field} w odpowiedzi"

