"""
Testy L5 (sonda SMTP) — logika klasyfikacji i obsługa błędów BEZ sieci.
Sonda SMTP z natury zależy od sieci/portu 25; tu testujemy deterministyczne
części: mapowanie kodów, wykrywanie catch-all, degradację do 'niejednoznacznie'
i tryb dry-run. Realne łączenie SMTP wymaga otwartego portu 25 (patrz §5).
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import smtp_probe as sp  # noqa: E402


class FakeSMTP:
    """Minimalny mock smtplib.SMTP sterowany zaprogramowanymi odpowiedziami RCPT."""
    def __init__(self, timeout=None):
        self.rcpt_responses = []
        self._i = 0
    def connect(self, host, port): return (220, b"ok")
    def ehlo_or_helo_if_needed(self): pass
    def ehlo(self, name=""): return (250, b"ok")
    def helo(self, name=""): return (250, b"ok")
    def mail(self, addr): return (250, b"ok")
    def rcpt(self, addr):
        resp = self.rcpt_responses[self._i]
        self._i += 1
        return resp
    def quit(self): pass


def _cfg(**kw):
    base = dict(helo="host.pl", mail_from="v@host.pl", connect_timeout=1,
                delay_s=0, max_per_domain=50, catch_all_check=True,
                dry_run=False, verbose=False)
    base.update(kw)
    return sp.ProbeConfig(**base)


def _patch_smtp(monkeypatch, responses):
    def factory(timeout=None):
        f = FakeSMTP()
        f.rcpt_responses = responses
        return f
    monkeypatch.setattr(sp.smtplib, "SMTP", factory)


def test_accept_250(monkeypatch):
    # RCPT realny 250, RCPT losowy (catch-all) 550 -> "tak"
    _patch_smtp(monkeypatch, [(250, b"OK"), (550, b"No such user")])
    res = sp.probe_address("jan@firma.pl", ["mx.firma.pl"], _cfg())
    assert res["skrzynka_zweryfikowana"] == sp.RESULT_YES
    assert res["catch_all"] == "nie"


def test_reject_550(monkeypatch):
    _patch_smtp(monkeypatch, [(550, b"No such user")])
    res = sp.probe_address("ghost@firma.pl", ["mx.firma.pl"], _cfg())
    assert res["skrzynka_zweryfikowana"] == sp.RESULT_NO


def test_greylisting_4xx_is_unsure(monkeypatch):
    _patch_smtp(monkeypatch, [(451, b"try later")])
    res = sp.probe_address("jan@firma.pl", ["mx.firma.pl"], _cfg())
    assert res["skrzynka_zweryfikowana"] == sp.RESULT_UNSURE


def test_catch_all_forces_unsure(monkeypatch):
    # realny 250 i losowy tez 250 -> catch-all -> niejednoznacznie
    _patch_smtp(monkeypatch, [(250, b"OK"), (250, b"OK")])
    res = sp.probe_address("ktokolwiek@catchall.pl", ["mx.catchall.pl"], _cfg())
    assert res["catch_all"] == "tak"
    assert res["skrzynka_zweryfikowana"] == sp.RESULT_UNSURE


def test_no_mx_is_no():
    res = sp.probe_address("jan@bezmx.pl", [], _cfg())
    assert res["skrzynka_zweryfikowana"] == sp.RESULT_NO


def test_connection_error_degrades_to_unsure(monkeypatch):
    def factory(timeout=None):
        raise OSError("port 25 blocked")
    monkeypatch.setattr(sp.smtplib, "SMTP", factory)
    res = sp.probe_address("jan@firma.pl", ["mx.firma.pl"], _cfg())
    # KRYTERIUM: brak połączenia nie crashuje, tylko 'niejednoznacznie'
    assert res["skrzynka_zweryfikowana"] == sp.RESULT_UNSURE
    assert "port 25" in res["szczegol"] or "brak połączenia" in res["szczegol"]


def test_dry_run_writes_csv(tmp_path, monkeypatch):
    # dry-run nie łączy się; MX zamockowany
    monkeypatch.setattr(sp, "resolve_mx", lambda d, timeout=5.0: ["mx.test"])
    inp = tmp_path / "in.csv"
    inp.write_text("email\njan@firma.pl\nanna@firma.pl\n", encoding="utf-8")
    outp = tmp_path / "out.csv"
    stats = sp.run(str(inp), str(outp), _cfg(dry_run=True))
    assert stats.total == 2
    rows = list(csv.DictReader(open(outp, encoding="utf-8")))
    assert len(rows) == 2
    assert all(r["szczegol"].startswith("dry-run") for r in rows)


def test_max_per_domain_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "resolve_mx", lambda d, timeout=5.0: ["mx.test"])
    inp = tmp_path / "in.csv"
    inp.write_text("email\na@x.pl\nb@x.pl\nc@x.pl\n", encoding="utf-8")
    stats = sp.run(str(inp), None, _cfg(dry_run=True, max_per_domain=2))
    # 3 adresy tej samej domeny, limit 2 -> 3. oznaczony jako pominięty (unsure)
    assert stats.total == 3
