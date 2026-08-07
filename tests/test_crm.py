"""
Testy opcjonalnej integracji CRM (app/crm.py).

Nie wymagaja realnej bazy - podmieniamy crm._connect na atrape (fake connection),
a flagi/konfiguracje ustawiamy przez monkeypatch na modul config.
"""
import pytest

from app import crm, config


class _FakeCursor:
    def __init__(self, row, raise_on_execute=False):
        self._row = row
        self._raise = raise_on_execute
        self.executed = None
        self.closed = False

    def execute(self, query, params=None):
        if self._raise:
            raise RuntimeError("boom")
        self.executed = (query, params)

    def fetchone(self):
        return self._row

    def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.fixture
def crm_enabled(monkeypatch):
    """Wlacza integracje z sensowna konfiguracja (host + baza)."""
    monkeypatch.setattr(config, "CRM_CHECK_ENABLED", True)
    monkeypatch.setattr(config, "CRM_DB_HOST", "db.local")
    monkeypatch.setattr(config, "CRM_DB_NAME", "crm")
    monkeypatch.setattr(config, "CRM_QUERY", "SELECT 1 FROM contacts WHERE email = %s LIMIT 1")


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(config, "CRM_CHECK_ENABLED", False)
    assert crm.is_enabled() is False
    assert crm.email_exists("jan@wp.pl") is None


def test_enabled_without_host_is_disabled(monkeypatch):
    monkeypatch.setattr(config, "CRM_CHECK_ENABLED", True)
    monkeypatch.setattr(config, "CRM_DB_HOST", "")
    monkeypatch.setattr(config, "CRM_DB_NAME", "")
    assert crm.is_enabled() is False
    assert crm.email_exists("jan@wp.pl") is None


def test_found_returns_true_and_lowercases(crm_enabled, monkeypatch):
    cur = _FakeCursor(row=(1,))
    conn = _FakeConn(cur)
    monkeypatch.setattr(crm, "_connect", lambda: conn)

    assert crm.email_exists("Jan@WP.pl") is True
    # adres bindowany parametrem, znormalizowany do lowercase
    assert cur.executed[1] == ("jan@wp.pl",)
    # zasoby posprzatane
    assert cur.closed is True
    assert conn.closed is True


def test_not_found_returns_false(crm_enabled, monkeypatch):
    conn = _FakeConn(_FakeCursor(row=None))
    monkeypatch.setattr(crm, "_connect", lambda: conn)
    assert crm.email_exists("nowy@wp.pl") is False


def test_empty_email_returns_none(crm_enabled, monkeypatch):
    # Nie powinnismy nawet probowac laczyc sie do bazy dla pustego adresu.
    def _boom():
        raise AssertionError("nie powinno byc polaczenia")

    monkeypatch.setattr(crm, "_connect", _boom)
    assert crm.email_exists("   ") is None


def test_connection_error_returns_none(crm_enabled, monkeypatch):
    def _fail():
        raise ConnectionError("brak polaczenia")

    monkeypatch.setattr(crm, "_connect", _fail)
    # Blad bazy nie wywraca walidacji - zwracamy None.
    assert crm.email_exists("jan@wp.pl") is None


def test_query_error_returns_none_and_closes(crm_enabled, monkeypatch):
    cur = _FakeCursor(row=None, raise_on_execute=True)
    conn = _FakeConn(cur)
    monkeypatch.setattr(crm, "_connect", lambda: conn)
    assert crm.email_exists("jan@wp.pl") is None
    assert conn.closed is True


def test_stats_hides_password(crm_enabled, monkeypatch):
    monkeypatch.setattr(config, "CRM_DB_PASSWORD", "tajne")
    s = crm.stats()
    assert s["enabled"] is True
    assert s["host"] == "db.local"
    assert "tajne" not in str(s)
