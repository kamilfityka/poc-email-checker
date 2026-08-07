"""Testy heurystyki zgodnosci imie/nazwisko <-> adres (app/name_match.py)."""
from app import name_match as n


def test_exact_match():
    r = n.evaluate("Kamil Fityka", "kamil.fityka@gmail.com")
    assert r["status"] == "match"
    assert r["suggestion"] is None
    assert r["source"] == "heuristic"


def test_typo_in_surname_gives_partial_and_suggestion():
    # Realny przypadek: literowka w nazwisku (Ftyka), adres jest poprawny.
    r = n.evaluate("Kamil Ftyka", "kamil.fityka@mail.com")
    assert r["status"] == "partial"
    assert r["suggestion"] == "Kamil Fityka"


def test_complete_mismatch():
    r = n.evaluate("Jan Kowalski", "anna.nowak@wp.pl")
    assert r["status"] == "mismatch"
    assert r["suggestion"] is None


def test_initial_plus_surname_is_partial():
    # Inicjal imienia + pelne nazwisko w adresie.
    r = n.evaluate("Malgorzata Wisniewska", "m.wisniewska@wp.pl")
    assert r["status"] in ("match", "partial")


def test_polish_diacritics_normalized():
    # Zażółć/ł/diakrytyki nie powinny psuc dopasowania.
    r = n.evaluate("Łukasz Żółć", "lukasz.zolc@wp.pl")
    assert r["status"] == "match"


def test_no_name_returns_unknown():
    r = n.evaluate("", "kamil@wp.pl")
    assert r["status"] == "unknown"


def test_too_short_localpart_returns_unknown():
    r = n.evaluate("Jan Kowalski", "jk@wp.pl")
    assert r["status"] == "unknown"


def test_suggestion_only_when_text_changes():
    # Gdy imie juz pasuje idealnie, nie proponujemy zmiany.
    r = n.evaluate("Kamil Fityka", "kamil.fityka@wp.pl")
    assert r["suggestion"] is None
