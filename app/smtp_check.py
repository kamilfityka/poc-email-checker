"""
Opcjonalna weryfikacja SMTP w czasie rzeczywistym (L5 w formularzu).

Wykonuje MX -> EHLO/HELO -> MAIL FROM -> RCPT TO:<adres> BEZ komendy DATA
(nie wysyla zadnej tresci) i wykrywa domeny catch-all. Twardy timeout, jedna
proba na pierwszy MX - by nie blokowac formularza.

Feature-flag przez runtime-toggle "smtp" (panel /admin). Domyslnie WYLACZONE.

Zasady (spojne z §6): wynik jest TYLKO informacyjny (pole smtp_check), nie
wplywa na block_save. Kazdy blad/timeout/greylisting -> "unknown".

RYZYKA (§5): sondowanie RCPT z naszego IP grozi wpisaniem na czarne listy i bywa
zawodne u duzych dostawcow (Google/Microsoft/Yahoo czesto zwracaja 250 na
wszystko). Dlatego domyslnie OFF i wlaczane swiadomie. Wariant offline/wsadowy:
scripts/smtp_probe.py.
"""
import logging
import random
import smtplib
import string
from typing import Optional

from . import config, runtime

logger = logging.getLogger("validator.smtp_check")

DELIVERABLE = "deliverable"      # RCPT 250, domena nie jest catch-all
UNDELIVERABLE = "undeliverable"  # RCPT 550/551/553/501 (odrzucenie)
RISKY = "risky"                  # 250, ale domena catch-all -> nie da sie potwierdzic
UNKNOWN = "unknown"              # greylisting/timeout/port 25/brak MX/wylaczone


def is_enabled() -> bool:
    return bool(runtime.enabled("smtp"))


def _resolve_mx(domain: str) -> list[str]:
    """Hosty MX wg priorytetu; [] gdy brak/timeout. Wydzielone dla testow."""
    import dns.resolver
    import dns.exception

    r = dns.resolver.Resolver()
    r.timeout = config.SMTP_CHECK_TIMEOUT_S
    r.lifetime = config.SMTP_CHECK_TIMEOUT_S
    try:
        answers = r.resolve(domain, "MX")
        mx = sorted(((a.preference, str(a.exchange).rstrip(".")) for a in answers),
                    key=lambda x: x[0])
        return [h for _, h in mx if h]
    except dns.exception.DNSException:
        return []


def _connect(host: str) -> smtplib.SMTP:
    """Otwiera polaczenie SMTP. Wydzielone, by dalo sie podmienic w testach."""
    server = smtplib.SMTP(timeout=config.SMTP_CHECK_TIMEOUT_S)
    server.connect(host, config.SMTP_CHECK_PORT)
    return server


def _rcpt(server: smtplib.SMTP, rcpt: str) -> int:
    """MAIL FROM + RCPT TO bez DATA. Zwraca kod odpowiedzi RCPT."""
    server.mail(config.SMTP_CHECK_MAIL_FROM)
    code, _ = server.rcpt(rcpt)
    return code


def _rand_local(n: int = 16) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _classify(code: int, catch_all: Optional[bool]) -> str:
    if code == 250:
        return RISKY if catch_all else DELIVERABLE
    if code in (550, 551, 553, 501):
        return UNDELIVERABLE
    return UNKNOWN  # 4xx (greylisting) i wszystko inne


def check(email: str) -> Optional[str]:
    """
    Zwraca deliverable|undeliverable|risky|unknown, albo None gdy warstwa
    wylaczona. Nigdy nie rzuca wyjatku (bledy -> "unknown").
    """
    if not is_enabled():
        return None

    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    domain = email.split("@", 1)[1]

    mx = _resolve_mx(domain)
    if not mx:
        # Brak MX to sygnal warstwy DNS (domain_not_found / no_mail_capability),
        # nie SMTP. My zwracamy "nie wiem".
        return UNKNOWN

    try:
        server = _connect(mx[0])
    except Exception:
        logger.info("smtp_check: brak polaczenia z %s (port %s)", mx[0], config.SMTP_CHECK_PORT)
        return UNKNOWN

    try:
        try:
            server.ehlo(config.SMTP_CHECK_HELO)
        except smtplib.SMTPException:
            server.helo(config.SMTP_CHECK_HELO)

        code = _rcpt(server, email)

        catch_all: Optional[bool] = None
        if config.SMTP_CHECK_CATCH_ALL and code == 250:
            try:
                ca_code = _rcpt(server, f"{_rand_local()}@{domain}")
                catch_all = (ca_code == 250)
            except smtplib.SMTPException:
                catch_all = None

        return _classify(code, catch_all)
    except Exception:
        logger.info("smtp_check: blad dialogu SMTP dla domeny %s", domain, exc_info=True)
        return UNKNOWN
    finally:
        try:
            server.quit()
        except Exception:
            pass


def stats() -> dict:
    """Podglad stanu warstwy - dla /config i panelu."""
    return {
        "enabled": is_enabled(),
        "helo": config.SMTP_CHECK_HELO,
        "port": config.SMTP_CHECK_PORT,
        "catch_all_check": config.SMTP_CHECK_CATCH_ALL,
    }
