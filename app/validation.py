"""
Rdzen walidacji: warstwy walidacji (§4 spec).

Kluczowa zasada (§1, §6): "poprawnosc" != "istnienie skrzynki". Potwierdzamy
tylko poprawnosc (skladnia, domena, MX). Wynikow niejednoznacznych (unknown)
NIGDY nie traktujemy jako bledu blokujacego.
"""
import time
from pathlib import Path
from typing import Optional

import dns.resolver
import dns.exception
from email_validator import validate_email, EmailNotValidError

from . import config, cache

_DATA = Path(__file__).parent / "data"


# --- ladowanie list slownikowych (raz, na starcie) --------------------------
def _load_lines(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            out.add(line)
    return out


POPULAR_DOMAINS = _load_lines(_DATA / "popular_domains.txt")
DISPOSABLE_DOMAINS = _load_lines(_DATA / "disposable_domains.txt")
ROLE_BASED = _load_lines(_DATA / "role_based.txt")


# --- resolver DNS (opcjonalny wlasny) ---------------------------------------
def _build_resolver() -> dns.resolver.Resolver:
    r = dns.resolver.Resolver(configure=True)
    if config.DNS_NAMESERVERS:
        r.nameservers = config.DNS_NAMESERVERS
    r.timeout = config.DNS_TIMEOUT_S
    r.lifetime = config.DNS_LIFETIME_S
    return r


_resolver = _build_resolver()


# --- skladnia (RFC 5321/5322) -----------------------------------------------
def check_syntax(email: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Zwraca (valid, local_part, domain). Walidacja bez rozsylania
    (check_deliverability=False - DNS robimy osobno, sterowany 'checks').
    """
    try:
        info = validate_email(email, check_deliverability=False)
        return True, info.local_part.lower(), info.domain.lower()
    except EmailNotValidError:
        return False, None, None


# --- literowki (did-you-mean), Damerau-Levenshtein --------------------------
def _damerau_levenshtein(a: str, b: str) -> int:
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,        # deletion
                d[i][j - 1] + 1,        # insertion
                d[i - 1][j - 1] + cost,  # substitution
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)  # transposition
    return d[la][lb]


def suggest_domain(domain: str) -> Optional[str]:
    """
    Zwraca najblizsza popularna domene, jesli jest "wystarczajaco blisko",
    ale rozna od wpisanej. Prog z konfiguracji, dobrany zeby nie poprawiac
    na sile (podejscie jak mailcheck.js).
    """
    if domain in POPULAR_DOMAINS:
        return None
    best, best_dist = None, config.TYPO_MAX_DISTANCE + 1
    for cand in POPULAR_DOMAINS:
        dist = _damerau_levenshtein(domain, cand)
        if dist < best_dist:
            best, best_dist = cand, dist
    if best and 0 < best_dist <= config.TYPO_MAX_DISTANCE:
        return best
    return None


# --- DNS A/AAAA + MX (z cache per-domena) -----------------------------------
def check_dns_mx(domain: str) -> dict:
    """
    Zwraca dict: {
        'domain_status': 'ok'|'not_found'|'unknown',
        'has_mx': bool|None,   # None gdy nie ustalono (np. unknown)
        'has_a': bool|None,
        'cached': bool,
    }
    Rozroznienie kluczowe (§6):
      NXDOMAIN            -> not_found  (autorytatywne "nie ma")
      timeout / SERVFAIL -> unknown    (nie wiemy -> nie blokujemy)
    """
    cached = cache.get(domain)
    if cached is not None:
        return {**cached, "cached": True}

    result = _resolve_domain(domain)
    if result["domain_status"] != "unknown":
        # Wynikow 'unknown' (chwilowe bledy) nie buforujemy - moga sie zmienic.
        cache.set(domain, result)
    return {**result, "cached": False}


def _query(domain: str, rdtype: str):
    return _resolver.resolve(domain, rdtype)


def _resolve_domain(domain: str) -> dict:
    has_a = None
    has_mx = None

    # A/AAAA
    try:
        try:
            _query(domain, "A")
            has_a = True
        except dns.resolver.NoAnswer:
            try:
                _query(domain, "AAAA")
                has_a = True
            except dns.resolver.NoAnswer:
                has_a = False
    except dns.resolver.NXDOMAIN:
        # Domena nie istnieje - mocny, autorytatywny sygnal.
        return {"domain_status": "not_found", "has_mx": False, "has_a": False}
    except (dns.resolver.NoNameservers, dns.exception.Timeout, dns.exception.DNSException):
        return {"domain_status": "unknown", "has_mx": None, "has_a": None}

    # MX
    try:
        answers = _query(domain, "MX")
        has_mx = len(answers) > 0
    except dns.resolver.NXDOMAIN:
        return {"domain_status": "not_found", "has_mx": False, "has_a": False}
    except dns.resolver.NoAnswer:
        has_mx = False
    except (dns.resolver.NoNameservers, dns.exception.Timeout, dns.exception.DNSException):
        # Domena istnieje (mielismy A) ale MX niepewne -> nie blokujemy.
        has_mx = None

    return {"domain_status": "ok", "has_mx": has_mx, "has_a": has_a}


# --- disposable / role-based ------------------------------------------------
def is_disposable(domain: str) -> bool:
    return domain in DISPOSABLE_DOMAINS


def is_role_based(local_part: str) -> bool:
    return local_part in ROLE_BASED


# --- Komunikaty PL (§7) ------------------------------------------------------
def message_for(result: str, *, domain: str = "", suggestion: str = "") -> str:
    return {
        "valid": "Adres wyglada poprawnie",
        "syntax_invalid": "Adres jest niepoprawny (sprawdz format)",
        "typo_suspected": f"Czy chodzilo o {suggestion}?",
        "domain_not_found": f"Domena {domain} nie istnieje",
        "no_mail_capability": f"Domena {domain} nie obsluguje poczty",
        "disposable": "To adres jednorazowy (domena tymczasowa)",
        "mailbox_not_found": "Nie potwierdzilismy tej skrzynki",
        "unknown": "Nie udalo sie w pelni zweryfikowac adresu",
    }.get(result, "Nie udalo sie w pelni zweryfikowac adresu")


# --- Orkiestracja: pelna walidacja jednego adresu ---------------------------
def validate(email: str, checks: list[str]) -> dict:
    """
    Uruchamia warstwy wg listy 'checks' i wyznacza 'result' wg priorytetu:
      syntax_invalid > typo_suspected > domain_not_found > no_mail_capability
      > disposable > unknown > valid
    Zwraca surowy dict pol odpowiedzi (bez block_save - to doklada warstwa API
    z config.resolve_block, zeby polityka byla poza kodem walidacji).
    """
    started = time.perf_counter()
    email = (email or "").strip()

    out = {
        "email": email,
        "result": "valid",
        "syntax_valid": True,
        "domain_status": "not_checked",
        "has_mx": None,
        "disposable": False,
        "role_based": False,
        "suggestion": None,
        "cached": False,
    }

    # skladnia (zawsze, to jedyna warstwa dajaca twardy blok)
    syntax_ok, local_part, domain = check_syntax(email)
    out["syntax_valid"] = syntax_ok
    if not syntax_ok:
        out["result"] = "syntax_invalid"
        out["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return out

    # role-based (flaga niezalezna od result)
    if "lists" in checks and local_part:
        out["role_based"] = is_role_based(local_part)

    # literowka. Jesli podejrzana -> short-circuit (domain_status
    # zostaje not_checked, zgodnie z przykladem w §6).
    if "typo" in checks:
        sugg = suggest_domain(domain)
        if sugg:
            suggested_email = f"{local_part}@{sugg}"
            out["suggestion"] = suggested_email
            out["result"] = "typo_suspected"
            out["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            return out

    # DNS + MX
    need_dns = "dns" in checks or "mx" in checks
    if need_dns:
        dns_res = check_dns_mx(domain)
        out["cached"] = dns_res.get("cached", False)
        status = dns_res["domain_status"]
        out["domain_status"] = status
        out["has_mx"] = dns_res.get("has_mx")

        if status == "not_found":
            out["result"] = "domain_not_found"
            out["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            return out

        if status == "unknown":
            out["result"] = "unknown"
            out["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            return out

        # status == ok. Sprawdzamy zdolnosc do przyjmowania poczty.
        has_mx = dns_res.get("has_mx")
        has_a = dns_res.get("has_a")
        # Brak MX i brak A -> domena nie obsluguje poczty.
        if has_mx is False and has_a is False:
            out["result"] = "no_mail_capability"
            out["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            return out
        # Brak MX ale jest A -> fallback wg RFC: "prawdopodobnie przyjmuje".

    # disposable (po DNS: domena istnieje, ale jest jednorazowa)
    if "lists" in checks and is_disposable(domain):
        out["disposable"] = True
        out["result"] = "disposable"
        out["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return out

    # Wszystko przeszlo -> valid
    out["result"] = "valid"
    out["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return out
