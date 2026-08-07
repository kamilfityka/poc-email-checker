"""
Opcjonalna integracja z CRM (MySQL/MariaDB).

Sprawdza, czy adres e-mail JUZ istnieje w bazie klientow (deduplikacja przy
dodawaniu nowego rekordu). Feature-flag - domyslnie WYLACZONE (CRM_CHECK_ENABLED).

Zasady (spojne z reszta serwisu, §6):
  - Gdy funkcja wylaczona / brak sterownika / brak konfiguracji / blad polaczenia
    -> zwracamy None ("nie wiemy"), NIGDY nie wywracamy walidacji poprawnosci.
  - Zapytanie jest konfigurowalne (CRM_QUERY) i wykonywane z bindowaniem
    parametru (%s) - adres nie jest sklejany w string (ochrona przed SQL injection).

Sterownik PyMySQL jest zaleznoscia OPCJONALNA - importujemy go leniwie, wiec
serwis dziala bez niego, o ile integracja pozostaje wylaczona.
"""
import logging
from typing import Optional

from . import config

logger = logging.getLogger("validator.crm")


def is_enabled() -> bool:
    """Integracja aktywna tylko gdy wlaczona flaga ORAZ podano host i baze."""
    return bool(config.CRM_CHECK_ENABLED and config.CRM_DB_HOST and config.CRM_DB_NAME)


def _connect():
    """Nawiazuje polaczenie do CRM. Wydzielone, by dalo sie podmienic w testach."""
    import pymysql  # leniwy import - zaleznosc opcjonalna

    return pymysql.connect(
        host=config.CRM_DB_HOST,
        port=config.CRM_DB_PORT,
        user=config.CRM_DB_USER,
        password=config.CRM_DB_PASSWORD,
        database=config.CRM_DB_NAME,
        connect_timeout=config.CRM_DB_TIMEOUT_S,
        read_timeout=config.CRM_DB_TIMEOUT_S,
        charset="utf8mb4",
        autocommit=True,
    )


def email_exists(email: str) -> Optional[bool]:
    """
    Zwraca:
      True  - adres jest w bazie CRM,
      False - adresu nie ma,
      None  - nie ustalono (funkcja wylaczona, brak sterownika/konfiguracji,
              timeout lub blad zapytania) -> walidacja poprawnosci sie nie zmienia.
    """
    if not is_enabled():
        return None

    email = (email or "").strip().lower()
    if not email:
        return None

    try:
        conn = _connect()
    except Exception:
        logger.warning("CRM: nie udalo sie polaczyc z baza %s", config.CRM_DB_HOST, exc_info=True)
        return None

    try:
        cur = conn.cursor()
        try:
            cur.execute(config.CRM_QUERY, (email,))
            row = cur.fetchone()
            return row is not None
        finally:
            cur.close()
    except Exception:
        logger.warning("CRM: blad zapytania do bazy", exc_info=True)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def stats() -> dict:
    """Podglad stanu integracji (bez hasla) - dla endpointu /config."""
    return {
        "enabled": is_enabled(),
        "configured": bool(config.CRM_DB_HOST and config.CRM_DB_NAME),
        "host": config.CRM_DB_HOST,
        "db": config.CRM_DB_NAME,
    }
