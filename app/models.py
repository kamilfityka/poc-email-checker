"""Modele request/response - kontrakt serwisu walidatora (§6 spec)."""
from typing import Literal, Optional
from pydantic import BaseModel, Field

# Dozwolone warstwy do uruchomienia (pole "checks" w request).
CheckName = Literal["syntax", "typo", "dns", "mx", "lists"]

# Mozliwe wartosci pola "result" (§6).
ResultCode = Literal[
    "valid",
    "syntax_invalid",
    "typo_suspected",
    "domain_not_found",
    "no_mail_capability",
    "disposable",
    "mailbox_not_found",
    "unknown",
]

# Status domeny w DNS.
DomainStatus = Literal["ok", "not_found", "unknown", "not_checked"]

# Zgodnosc imie/nazwisko <-> adres (warstwa AI/heurystyka).
NameMatch = Literal["match", "partial", "mismatch", "unknown"]

# Wynik warstwy SMTP check (L5 real-time).
SmtpCheck = Literal["deliverable", "undeliverable", "risky", "unknown"]


class ValidateRequest(BaseModel):
    email: str
    checks: list[CheckName] = Field(
        default_factory=lambda: ["syntax", "typo", "dns", "mx", "lists"]
    )
    # Opcjonalne imie i nazwisko - gdy podane, liczymy zgodnosc z adresem
    # (heurystyka + opcjonalne AI). Puste/brak -> warstwa pomijana.
    name: Optional[str] = None


class ValidateResponse(BaseModel):
    # --- pola z kontraktu §6 (kolejnosc jak w przykladzie) ---
    email: str
    result: ResultCode
    block_save: bool
    syntax_valid: bool
    domain_status: DomainStatus
    has_mx: Optional[bool] = None
    disposable: bool = False
    role_based: bool = False
    suggestion: Optional[str] = None
    message_pl: str
    cached: bool = False
    elapsed_ms: int
    # --- pole dodatkowe (additive), potrzebne UI do checkboxa "potwierdzam recznie" ---
    block_override_allowed: bool = False
    # --- opcjonalna integracja CRM (deduplikacja): True/False, None gdy nie sprawdzano ---
    exists_in_crm: Optional[bool] = None
    # --- zgodnosc imie/nazwisko <-> adres (heurystyka + opcjonalne AI) ---
    # None gdy nie podano 'name' lub warstwa wylaczona. Zadne z tych pol nie
    # wplywa na block_save - to sygnal informacyjny.
    name_email_match: Optional[NameMatch] = None
    name_suggestion: Optional[str] = None
    name_match_source: Optional[Literal["heuristic", "ai"]] = None
    # --- opcjonalny SMTP check (L5 real-time): None gdy warstwa wylaczona ---
    smtp_check: Optional[SmtpCheck] = None


class AdminSettingsRequest(BaseModel):
    """Zmiana przelacznikow warstw opcjonalnych z panelu /admin.

    Kazde pole opcjonalne - ustawiamy tylko te faktycznie podane (dowolny
    podzbior; wszystkie None = brak zmian, tylko odczyt).
    """
    ai: Optional[bool] = None
    crm: Optional[bool] = None
    smtp: Optional[bool] = None


class VerifySendRequest(BaseModel):
    email: str


class VerifySendResponse(BaseModel):
    status: Literal["sent", "already_confirmed"] = "sent"


class VerifyConfirmResponse(BaseModel):
    email: str
    confirmed: bool


class VerifyStatusResponse(BaseModel):
    email: str
    confirmed: bool
    pending: bool
