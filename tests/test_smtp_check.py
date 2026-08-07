"""
Testy real-time SMTP check (app/smtp_check.py).

Bez realnej sieci - podmieniamy _resolve_mx i _connect na atrapy, a przelacznik
wlaczamy przez runtime.set_enabled("smtp", True).
"""
import smtplib

import pytest

from app import smtp_check, runtime


class _FakeSMTP:
    """Atrapa polaczenia SMTP. rcpt_codes: kolejne kody dla kolejnych RCPT."""
    def __init__(self, rcpt_codes):
        self._codes = list(rcpt_codes)
        self.quit_called = False

    def ehlo(self, helo):
        return (250, b"ok")

    def helo(self, helo):
        return (250, b"ok")

    def mail(self, sender):
        return (250, b"ok")

    def rcpt(self, addr):
        code = self._codes.pop(0)
        return (code, b"resp")

    def quit(self):
        self.quit_called = True


@pytest.fixture
def smtp_on():
    runtime.set_enabled("smtp", True)


def _wire(monkeypatch, server, mx=("mx.example.com",)):
    monkeypatch.setattr(smtp_check, "_resolve_mx", lambda d: list(mx))
    monkeypatch.setattr(smtp_check, "_connect", lambda host: server)


def test_disabled_returns_none(monkeypatch):
    runtime.set_enabled("smtp", False)
    # Nie powinno nawet ruszac DNS/polaczenia.
    monkeypatch.setattr(smtp_check, "_resolve_mx", lambda d: (_ for _ in ()).throw(AssertionError("nie ruszaj")))
    assert smtp_check.check("jan@wp.pl") is None


def test_deliverable(monkeypatch, smtp_on):
    # RCPT 250 dla adresu, 550 dla losowej sondy catch-all -> deliverable.
    _wire(monkeypatch, _FakeSMTP([250, 550]))
    assert smtp_check.check("jan@wp.pl") == smtp_check.DELIVERABLE


def test_catch_all_is_risky(monkeypatch, smtp_on):
    # 250 dla adresu i 250 dla losowej sondy -> domena accept-all -> risky.
    _wire(monkeypatch, _FakeSMTP([250, 250]))
    assert smtp_check.check("jan@wp.pl") == smtp_check.RISKY


def test_undeliverable(monkeypatch, smtp_on):
    _wire(monkeypatch, _FakeSMTP([550]))
    assert smtp_check.check("nieistnieje@wp.pl") == smtp_check.UNDELIVERABLE


def test_greylisting_is_unknown(monkeypatch, smtp_on):
    _wire(monkeypatch, _FakeSMTP([451]))
    assert smtp_check.check("jan@wp.pl") == smtp_check.UNKNOWN


def test_no_mx_is_unknown(monkeypatch, smtp_on):
    monkeypatch.setattr(smtp_check, "_resolve_mx", lambda d: [])
    assert smtp_check.check("jan@niema-mx.example") == smtp_check.UNKNOWN


def test_connection_error_is_unknown(monkeypatch, smtp_on):
    monkeypatch.setattr(smtp_check, "_resolve_mx", lambda d: ["mx.example.com"])

    def _boom(host):
        raise ConnectionRefusedError("port 25 zablokowany")

    monkeypatch.setattr(smtp_check, "_connect", _boom)
    assert smtp_check.check("jan@wp.pl") == smtp_check.UNKNOWN


def test_quit_called_on_success(monkeypatch, smtp_on):
    server = _FakeSMTP([250, 550])
    _wire(monkeypatch, server)
    smtp_check.check("jan@wp.pl")
    assert server.quit_called is True
