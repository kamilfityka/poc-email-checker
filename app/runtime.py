"""
Runtime-przelaczniki warstw opcjonalnych (AI / CRM / SMTP).

Panel /admin pozwala wlaczac i wylaczac te warstwy "w locie", bez restartu i bez
zmiany ENV. Wartosci domyslne biora sie z feature-flag w config (ENV), a
nadpisania z panelu sa UTRWALANE w jednym pliku JSON (RUNTIME_SETTINGS_PATH),
wiec przetrwaja restart procesu.

Zasada: sam przelacznik = "chce miec to wlaczone". To, czy warstwa REALNIE
dziala, i tak weryfikuje `is_enabled()` w danym module (musi byc jeszcze
konfiguracja: host/baza dla CRM, base_url/model dla AI itd.).
"""
import json
import logging
import threading
from pathlib import Path

from . import config

logger = logging.getLogger("validator.runtime")

# Klucze przelacznikow wystawiane w panelu.
TOGGLE_KEYS = ("ai", "crm", "smtp")

_LOCK = threading.Lock()
_overrides: dict[str, bool] = {}   # tylko klucze faktycznie nadpisane z panelu


def _defaults() -> dict[str, bool]:
    """Domyslne (z ENV) czytane na biezaco - by monkeypatch w testach dzialal."""
    return {
        "ai": bool(config.AI_ENABLED),
        "crm": bool(config.CRM_CHECK_ENABLED),
        "smtp": bool(config.SMTP_CHECK_ENABLED),
    }


def _load() -> None:
    """Wczytuje nadpisania z pliku (best-effort). Brak/uszkodzony plik = pusto."""
    global _overrides
    try:
        data = json.loads(Path(config.RUNTIME_SETTINGS_PATH).read_text(encoding="utf-8"))
        _overrides = {k: bool(v) for k, v in data.items() if k in TOGGLE_KEYS}
    except FileNotFoundError:
        _overrides = {}
    except Exception:
        logger.warning("runtime: nie udalo sie wczytac %s", config.RUNTIME_SETTINGS_PATH, exc_info=True)
        _overrides = {}


def _save() -> None:
    """Zapisuje nadpisania do pliku (best-effort - blad nie wywraca serwisu)."""
    try:
        Path(config.RUNTIME_SETTINGS_PATH).write_text(
            json.dumps(_overrides, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        logger.warning("runtime: nie udalo sie zapisac %s", config.RUNTIME_SETTINGS_PATH, exc_info=True)


def enabled(key: str) -> bool:
    """Czy dana warstwa jest wlaczona (nadpisanie z panelu > domyslne z ENV)."""
    if key in _overrides:
        return _overrides[key]
    return _defaults().get(key, False)


def set_enabled(key: str, value: bool) -> None:
    """Ustawia i utrwala przelacznik. Nieznany klucz jest ignorowany."""
    if key not in TOGGLE_KEYS:
        raise ValueError(f"nieznany przelacznik: {key}")
    with _LOCK:
        _overrides[key] = bool(value)
        _save()


def apply(changes: dict) -> dict:
    """Ustawia podzbior przelacznikow naraz. Zwraca pelny stan po zmianie."""
    for key, value in changes.items():
        if key in TOGGLE_KEYS and value is not None:
            set_enabled(key, value)
    return states()


def states() -> dict[str, bool]:
    """Aktualny stan wszystkich przelacznikow."""
    return {k: enabled(k) for k in TOGGLE_KEYS}


def reset() -> None:
    """Czysci nadpisania (bez zapisu) - uzywane w testach."""
    global _overrides
    _overrides = {}


# Wczytaj utrwalone nadpisania przy starcie procesu.
_load()
