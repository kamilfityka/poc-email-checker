"""
Konfiguracja serwisu walidatora.

Zgodnie z §8 spec: "Progi i to, ktore stany blokuja, trzymamy w konfiguracji
serwisu (nie w kodzie)". Wszystko ponizej mozna nadpisac zmiennymi srodowiskowymi
- kod nie zawiera zaszytych na sztywno regul blokowania.
"""
import os


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on", "tak")


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val is not None else default


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


# --- Tryby blokowania zapisu -------------------------------------------------
# hard        -> block_save=True,  override niedozwolony (twardy blok)
# conditional -> block_save=True,  override dozwolony (ostrzezenie + checkbox
#                "potwierdzam recznie")
# warn        -> block_save=False, tylko ostrzezenie
# none        -> block_save=False, brak jakiejkolwiek blokady
BLOCK_MODES = {
    "hard": {"block_save": True, "override_allowed": False},
    "conditional": {"block_save": True, "override_allowed": True},
    "warn": {"block_save": False, "override_allowed": False},
    "none": {"block_save": False, "override_allowed": False},
}

# Mapowanie: wynik (result) -> tryb blokowania. To jest "polityka" serwisu.
# Domyslne wartosci wprost z §8 specyfikacji.
RESULT_POLICY = {
    "valid": os.getenv("POLICY_VALID", "none"),
    "syntax_invalid": os.getenv("POLICY_SYNTAX_INVALID", "hard"),
    "typo_suspected": os.getenv("POLICY_TYPO_SUSPECTED", "warn"),
    "domain_not_found": os.getenv("POLICY_DOMAIN_NOT_FOUND", "conditional"),
    "no_mail_capability": os.getenv("POLICY_NO_MAIL_CAPABILITY", "conditional"),
    "disposable": os.getenv("POLICY_DISPOSABLE", "warn"),
    "mailbox_not_found": os.getenv("POLICY_MAILBOX_NOT_FOUND", "warn"),
    "unknown": os.getenv("POLICY_UNKNOWN", "none"),  # §6: NIGDY nie blokuje
}


def resolve_block(result: str) -> dict:
    """Zwraca {'block_save': bool, 'override_allowed': bool} dla danego wyniku."""
    mode = RESULT_POLICY.get(result, "none")
    return BLOCK_MODES.get(mode, BLOCK_MODES["none"])


# --- DNS / cache -------------------------------------------------------------
DNS_TIMEOUT_S = _env_float("DNS_TIMEOUT_S", 1.5)      # §9 twardy timeout
DNS_LIFETIME_S = _env_float("DNS_LIFETIME_S", 2.0)    # laczny budzet na zapytanie
CACHE_TTL_S = _env_int("CACHE_TTL_S", 6 * 3600)       # 6-24h
CACHE_MAXSIZE = _env_int("CACHE_MAXSIZE", 10_000)

# Opcjonalny wlasny/firmowy resolver DNS. Pusta wartosc = systemowy.
# Format: "10.0.0.53,10.0.0.54"
DNS_NAMESERVERS = [
    s.strip() for s in os.getenv("DNS_NAMESERVERS", "").split(",") if s.strip()
]

# --- Typo (server-side) ------------------------------------------------------
# Prog odleglosci edycyjnej dla sugestii domeny.
TYPO_MAX_DISTANCE = _env_int("TYPO_MAX_DISTANCE", 2)

# --- Double opt-in -----------------------------------------------------------
VERIFY_TTL_S = _env_int("VERIFY_TTL_S", 24 * 3600)    # TTL tokenu ~24h
VERIFY_BASE_URL = os.getenv("VERIFY_BASE_URL", "http://localhost:8000")

# Store tokenow: sqlite (trwaly, jeden plik) lub memory (gubi po restarcie).
VERIFY_STORE = os.getenv("VERIFY_STORE", "sqlite").strip().lower()
VERIFY_DB_PATH = os.getenv("VERIFY_DB_PATH", "verify.db")

# Branding maila / stron potwierdzenia.
VERIFY_COMPANY_NAME = os.getenv("VERIFY_COMPANY_NAME", "Nasza firma")
VERIFY_SUBJECT = os.getenv("VERIFY_SUBJECT", "Potwierdz swoj adres e-mail")
VERIFY_LOGO_URL = os.getenv("VERIFY_LOGO_URL", "")

# Limit wysylek na adres (ochrona przed naduzyciem/spamem). 0 = bez limitu.
VERIFY_RATE_MAX = _env_int("VERIFY_RATE_MAX", 3)
VERIFY_RATE_WINDOW_S = _env_int("VERIFY_RATE_WINDOW_S", 3600)

SMTP_HOST = os.getenv("SMTP_HOST", "")                # istniejacy relay Outlook/Exchange
SMTP_PORT = _env_int("SMTP_PORT", 25)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "noreply@example.com")
SMTP_STARTTLS = _env_bool("SMTP_STARTTLS", False)
# Jesli brak SMTP_HOST albo SMTP_DRY_RUN=true -> maila nie wysylamy naprawde,
# tylko logujemy (tryb PoC/dev).
SMTP_DRY_RUN = _env_bool("SMTP_DRY_RUN", not bool(SMTP_HOST))

# --- Ogolne ------------------------------------------------------------------
# CORS - w PoC szeroko; docelowo zawezic do origin CRM.
CORS_ORIGINS = [s.strip() for s in os.getenv("CORS_ORIGINS", "*").split(",")]
LOG_FULL_EMAIL = _env_bool("LOG_FULL_EMAIL", False)   # §13: minimalizacja w logach
