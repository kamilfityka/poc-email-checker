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
CACHE_TTL_S = _env_int("CACHE_TTL_S", 6 * 3600)       # §L3: 6-24h
CACHE_MAXSIZE = _env_int("CACHE_MAXSIZE", 10_000)

# Opcjonalny wlasny/firmowy resolver DNS (§L2). Pusta wartosc = systemowy.
# Format: "10.0.0.53,10.0.0.54"
DNS_NAMESERVERS = [
    s.strip() for s in os.getenv("DNS_NAMESERVERS", "").split(",") if s.strip()
]

# --- Typo / L1 (server-side) -------------------------------------------------
# Prog odleglosci edycyjnej dla sugestii domeny.
TYPO_MAX_DISTANCE = _env_int("TYPO_MAX_DISTANCE", 2)

# --- CRM lookup (opcjonalne, MySQL/MariaDB) ----------------------------------
# Feature-flag: sprawdza, czy adres JUZ istnieje w bazie CRM (deduplikacja).
# Domyslnie WYLACZONE - gdy off lub brak konfiguracji, /validate dziala jak dotad.
# Wynik jest tylko informacyjny (pole exists_in_crm) - nie wplywa na block_save.
CRM_CHECK_ENABLED = _env_bool("CRM_CHECK_ENABLED", False)
CRM_DB_HOST = os.getenv("CRM_DB_HOST", "")
CRM_DB_PORT = _env_int("CRM_DB_PORT", 3306)
CRM_DB_USER = os.getenv("CRM_DB_USER", "")
CRM_DB_PASSWORD = os.getenv("CRM_DB_PASSWORD", "")
CRM_DB_NAME = os.getenv("CRM_DB_NAME", "")
CRM_DB_TIMEOUT_S = _env_int("CRM_DB_TIMEOUT_S", 2)     # twardy timeout polaczenia/odczytu
# Zapytanie MUSI zawierac dokladnie jeden placeholder %s (adres e-mail, lowercased).
# Zwrocenie >=1 wiersza => adres istnieje w CRM. Bindowanie parametrem (anty-SQLi).
CRM_QUERY = os.getenv("CRM_QUERY", "SELECT 1 FROM contacts WHERE email = %s LIMIT 1")

# --- Zgodnosc imie/nazwisko <-> adres: heurystyka + opcjonalne AI ------------
# Heurystyka jest tania i deterministyczna; dziala tylko gdy w zadaniu podano
# 'name'. Nie ma wlasnego przelacznika w panelu - to baza, ktora zawsze liczymy.
NAME_MATCH_ENABLED = _env_bool("NAME_MATCH_ENABLED", True)
NAME_MATCH_MIN_RATIO = _env_float("NAME_MATCH_MIN_RATIO", 0.85)  # prog "blisko" (literowka)

# Opcjonalny WLASNY model AI (OpenAI-compatible /chat/completions). Domyslnie OFF.
# Przelacznik "ai" w panelu wlacza dopracowywanie werdyktu heurystyki modelem.
AI_ENABLED = _env_bool("AI_ENABLED", False)
AI_BASE_URL = os.getenv("AI_BASE_URL", "").rstrip("/")   # np. http://twoj-llm:8000/v1
AI_MODEL = os.getenv("AI_MODEL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_TIMEOUT_S = _env_float("AI_TIMEOUT_S", 4.0)

# --- Panel administracyjny + runtime toggles ---------------------------------
# Jesli ustawione, panel /admin oraz /admin/settings wymagaja naglowka
# X-Admin-Token. Puste = otwarte (tylko PoC/dev).
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
# Plik z runtime-nadpisaniami przelacznikow (AI/CRM) ustawianych z panelu.
# Utrwalane, wiec przetrwaja restart. Pojedynczy plik JSON.
RUNTIME_SETTINGS_PATH = os.getenv("RUNTIME_SETTINGS_PATH", "runtime_settings.json")

# --- Ogolne ------------------------------------------------------------------
# CORS - w PoC szeroko; docelowo zawezic do origin CRM.
CORS_ORIGINS = [s.strip() for s in os.getenv("CORS_ORIGINS", "*").split(",")]
LOG_FULL_EMAIL = _env_bool("LOG_FULL_EMAIL", False)   # §13: minimalizacja w logach
