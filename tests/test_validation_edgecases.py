"""
Testy EDGE-CASE dla rdzenia walidacji (app/validation.py).

Skupiaja sie na granicznych i latwych do przeoczenia zachowaniach, ktore nie sa
pokryte przez tests/test_validation.py:
  - priorytet wynikow (co "wygrywa", gdy pasuje kilka warstw naraz),
  - normalizacja wejscia (puste/None/spacje/wielkosc liter),
  - bramkowanie warstw przez liste 'checks',
  - granice DNS/MX (has_mx None vs False, has_a False),
  - dokladnosc metryki literowek (Damerau-Levenshtein z transpozycja).

DNS jest zawsze mockowany - testy sa szybkie i niezalezne od sieci.
"""
import pytest

from app import validation, config


def _mock_dns(monkeypatch, status, has_mx=True, has_a=True):
    """Podmienia check_dns_mx na deterministyczny wynik (bez sieci)."""
    monkeypatch.setattr(
        validation, "check_dns_mx",
        lambda domain: {"domain_status": status, "has_mx": has_mx,
                        "has_a": has_a, "cached": False},
    )


ALL_CHECKS = ["syntax", "typo", "dns", "mx", "lists"]
# Realny wpis ze slownika domen jednorazowych (app/data/disposable_domains.txt).
DISPOSABLE = "0-mail.com"


# --- 1-4: normalizacja wejscia ----------------------------------------------
def test_empty_email_is_syntax_invalid():
    """Pusty string nie ma szans przejsc L0 -> twardy blok."""
    out = validation.validate("", ALL_CHECKS)
    assert out["result"] == "syntax_invalid"
    assert out["syntax_valid"] is False


def test_none_email_is_syntax_invalid():
    """validate() musi zniesc None (front moze przyslac null) - bez wyjatku."""
    out = validation.validate(None, ALL_CHECKS)
    assert out["result"] == "syntax_invalid"


def test_whitespace_only_email_is_syntax_invalid():
    """Same spacje po strip() = pusto -> syntax_invalid."""
    out = validation.validate("   \t  ", ALL_CHECKS)
    assert out["result"] == "syntax_invalid"


def test_surrounding_whitespace_is_trimmed(monkeypatch):
    """Wiodace/koncowe spacje sa obcinane, a pole 'email' oczyszczone."""
    _mock_dns(monkeypatch, "ok", has_mx=True, has_a=True)
    out = validation.validate("  jan@moja-firma-abc.pl  ", ALL_CHECKS)
    assert out["result"] == "valid"
    assert out["email"] == "jan@moja-firma-abc.pl"


# --- 5-7: bramkowanie warstw i wielkosc liter -------------------------------
def test_uppercase_domain_typo_suggestion_is_lowercased():
    """Literowka w domenie pisanej wielkimi literami -> sugestia malymi."""
    out = validation.validate("Jan@GMIAL.COM", ["syntax", "typo"])
    assert out["result"] == "typo_suspected"
    assert out["suggestion"] == "jan@gmail.com"


def test_typo_not_detected_when_not_in_checks(monkeypatch):
    """Bez 'typo' w checks literowka nie jest wykrywana - domena idzie do DNS."""
    _mock_dns(monkeypatch, "ok", has_mx=True, has_a=True)
    out = validation.validate("jan@gmial.com", ["syntax", "dns", "mx"])
    assert out["result"] == "valid"
    assert out["suggestion"] is None


def test_syntax_invalid_beats_typo():
    """Priorytet: bledna skladnia wygrywa nad literowka (nie sugerujemy nic)."""
    out = validation.validate("zla@@gmial.com", ["syntax", "typo"])
    assert out["result"] == "syntax_invalid"
    assert out["suggestion"] is None


# --- 8-9: suggest_domain (granice slownika) ---------------------------------
def test_suggest_domain_exact_popular_returns_none():
    """Poprawna, popularna domena nie dostaje sugestii (0 dystansu)."""
    assert validation.suggest_domain("gmail.com") is None


def test_suggest_domain_far_from_dictionary_returns_none():
    """Domena firmowa daleko od slownika -> brak sugestii (nie poprawiamy na sile)."""
    assert validation.suggest_domain("moja-firma.pl") is None


# --- 10-11: Damerau-Levenshtein (dokladnosc metryki) ------------------------
@pytest.mark.parametrize("a,b,expected", [
    ("gmial", "gmail", 1),   # transpozycja sasiednich znakow = 1 (nie 2)
    ("ab", "ba", 1),         # czysta transpozycja
    ("", "abc", 3),          # z pustego: 3 wstawienia
    ("abc", "abc", 0),       # identyczne
])
def test_damerau_levenshtein(a, b, expected):
    assert validation._damerau_levenshtein(a, b) == expected


def test_damerau_levenshtein_is_symmetric():
    """Odleglosc edycyjna jest symetryczna - kolejnosc argumentow bez znaczenia."""
    assert (validation._damerau_levenshtein("gmial.com", "gmail.com")
            == validation._damerau_levenshtein("gmail.com", "gmial.com"))


# --- 12-15: disposable a priorytety i bramkowanie ---------------------------
# Bez 'typo' - inaczej niektore domeny jednorazowe lapia sie jako literowka
# (warstwa typo ma wyzszy priorytet); tutaj testujemy priorytet DNS vs disposable.
_NO_TYPO = ["syntax", "dns", "mx", "lists"]


def test_domain_not_found_beats_disposable(monkeypatch):
    """Priorytet: nieistniejaca domena wygrywa nad flaga 'jednorazowa'."""
    _mock_dns(monkeypatch, "not_found", has_mx=False, has_a=False)
    out = validation.validate(f"jan@{DISPOSABLE}", _NO_TYPO)
    assert out["result"] == "domain_not_found"


def test_unknown_dns_beats_disposable(monkeypatch):
    """DNS 'unknown' (chwilowy blad) short-circuituje PRZED sprawdzeniem disposable."""
    _mock_dns(monkeypatch, "unknown", has_mx=None, has_a=None)
    out = validation.validate(f"jan@{DISPOSABLE}", _NO_TYPO)
    assert out["result"] == "unknown"
    assert config.resolve_block(out["result"])["block_save"] is False


def test_disposable_detected_without_dns_checks():
    """Bez warstw DNS (need_dns=False) disposable dziala od razu na liscie."""
    out = validation.validate(f"jan@{DISPOSABLE}", ["syntax", "lists"])
    assert out["result"] == "disposable"
    assert out["disposable"] is True
    assert out["domain_status"] == "not_checked"


def test_disposable_not_flagged_without_lists(monkeypatch):
    """Bez 'lists' w checks domena jednorazowa przechodzi jako valid."""
    _mock_dns(monkeypatch, "ok", has_mx=True, has_a=True)
    out = validation.validate(f"jan@{DISPOSABLE}", ["syntax", "dns", "mx"])
    assert out["result"] == "valid"
    assert out["disposable"] is False


# --- 16-18: granice MX/A ----------------------------------------------------
def test_no_mail_capability_requires_no_mx_and_no_a(monkeypatch):
    """no_mail_capability tylko gdy brak MX I brak A jednoczesnie."""
    _mock_dns(monkeypatch, "ok", has_mx=False, has_a=False)
    out = validation.validate("jan@example-nomail.pl", ["syntax", "dns", "mx"])
    assert out["result"] == "no_mail_capability"


def test_unknown_mx_with_a_record_is_valid(monkeypatch):
    """has_mx=None (MX niepewne) + jest A -> nie blokujemy, wynik valid."""
    _mock_dns(monkeypatch, "ok", has_mx=None, has_a=True)
    out = validation.validate("jan@only-a-record.pl", ["syntax", "dns", "mx"])
    assert out["result"] == "valid"


def test_mx_present_without_a_is_valid(monkeypatch):
    """Jest MX ale brak A -> to wystarcza do przyjmowania poczty (valid)."""
    _mock_dns(monkeypatch, "ok", has_mx=True, has_a=False)
    out = validation.validate("jan@mx-only.pl", ["syntax", "dns", "mx"])
    assert out["result"] == "valid"


# --- 19-20: flagi informacyjne i kontrakt polityki --------------------------
def test_role_based_flag_does_not_change_result(monkeypatch):
    """role_based to tylko sygnal informacyjny - nie zmienia 'result' ani bloku."""
    _mock_dns(monkeypatch, "ok", has_mx=True, has_a=True)
    out = validation.validate("biuro@moja-firma-abc.pl", ALL_CHECKS)
    assert out["role_based"] is True
    assert out["result"] == "valid"
    assert config.resolve_block(out["result"])["block_save"] is False


def test_policy_contract_unknown_never_blocks_but_syntax_is_hard():
    """Kontrakt §6: 'unknown' nigdy nie blokuje; 'syntax_invalid' to twardy blok."""
    assert config.resolve_block("unknown")["block_save"] is False
    hard = config.resolve_block("syntax_invalid")
    assert hard["block_save"] is True
    assert hard["override_allowed"] is False
