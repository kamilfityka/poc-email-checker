"""
Double opt-in / kod weryfikacyjny (spec).

Jedyna metoda dajaca 100% pewnosc istnienia skrzynki. Wszystko w TYM SAMYM
serwisie: /verify/send generuje token (store z TTL ~24h) i wysyla mail przez
istniejacy relay Outlook/Exchange (multipart tekst+HTML); /verify/confirm
oznacza potwierdzenie. Wysylka "w tle" (BackgroundTasks) - patrz main.py.

Wazne dla prywatnych skrzynek: double opt-in wysyla JEDEN normalny mail transakcyjny przez
Wasz istniejacy relay/IP - nie sonduje cudzych serwerow, wiec nie grozi
wpisaniem IP na czarne listy. Reputacje chronimy przez to, ze walidacja odsiewa
bledy PRZED wyslaniem (niski bounce rate).
"""
import logging
import secrets
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config
from . import verify_templates as tpl
from .verify_store import store

logger = logging.getLogger("validator.verify")


class RateLimited(Exception):
    """Przekroczono limit wysylek dla danego adresu."""


def is_confirmed(email: str) -> bool:
    return store.is_confirmed(email)


def has_pending(email: str) -> bool:
    return store.has_pending(email)


def confirm(token: str):
    """
    Potwierdza token. Zwraca (status, email):
      status in {'confirmed','already','invalid'}
    """
    # Czy juz zuzyty/potwierdzony? peek zwroci None jesli nie istnieje/wygasl.
    email = store.pop_token(token)
    if email is None:
        return "invalid", None
    if store.is_confirmed(email):
        # (rzadki wyscig: potwierdzony innym tokenem) - traktuj jako 'already'
        return "already", email
    store.mark_confirmed(email)
    return "confirmed", email


def create_and_send(email: str, background_add) -> str:
    """
    Generuje token, planuje wysylke w tle i zwraca status:
      'already_confirmed' | 'sent'
    Rzuca RateLimited przy przekroczeniu limitu.
    """
    if store.is_confirmed(email):
        return "already_confirmed"

    if config.VERIFY_RATE_MAX > 0:
        recent = store.recent_send_count(email, config.VERIFY_RATE_WINDOW_S)
        if recent >= config.VERIFY_RATE_MAX:
            raise RateLimited()

    token = secrets.token_urlsafe(32)
    store.save_token(token, email, config.VERIFY_TTL_S)
    store.record_send(email)
    background_add(send_verification, email, token)
    return "sent"


def _build_message(email: str, token: str) -> MIMEMultipart:
    link = f"{config.VERIFY_BASE_URL}/verify/confirm?token={token}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = config.VERIFY_SUBJECT
    msg["From"] = config.SMTP_FROM
    msg["To"] = email
    msg.attach(MIMEText(tpl.email_plain(link), "plain", "utf-8"))
    msg.attach(MIMEText(tpl.email_html(link), "html", "utf-8"))
    return msg


def send_verification(email: str, token: str) -> None:
    """
    Wysyla mail weryfikacyjny przez istniejacy relay (smtplib).
    Dry-run (brak SMTP_HOST lub SMTP_DRY_RUN=true) -> tylko loguje link.
    """
    link = f"{config.VERIFY_BASE_URL}/verify/confirm?token={token}"

    if config.SMTP_DRY_RUN:
        logger.info("[DRY-RUN] Mail weryfikacyjny (nie wyslano). Link: %s", link)
        print(f"[DRY-RUN] Link weryfikacyjny: {link}")
        return

    msg = _build_message(email, token)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
            if config.SMTP_STARTTLS:
                s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        logger.info("Mail weryfikacyjny wyslany przez relay %s", config.SMTP_HOST)
    except Exception:
        logger.exception("Blad wysylki maila weryfikacyjnego")
