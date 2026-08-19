"""
Walidacja wsadowa (batch) z pliku CSV.

Po co: pojedyncze `/validate` obsluguje formularz w czasie rzeczywistym, ale do
oceny jakosci istniejacej bazy kontaktow potrzebny jest przebieg hurtowy -
"wgraj CSV (id, email, imie, nazwisko), zobacz raport".

Zasady sa te same co w /validate (§6, §8): ta sama funkcja `validation.validate`,
ta sama polityka blokowania z `config.resolve_block`, ta sama heurystyka
zgodnosci imie/nazwisko. Batch NIE wprowadza wlasnych regul - tylko zbiera
wyniki, deduplikuje powtorzone adresy i liczy statystyki.

Wydajnosc: DNS to operacja I/O, wiec unikalne adresy lecą przez pulę watkow
(`BATCH_MAX_WORKERS`), a cache DNS per-domena (`app/cache.py`) sprawia, ze
1000 adresow z kilkudziesieciu domen to kilkadziesiat zapytan DNS.
"""
import csv
import io
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config, name_match, ai_client
from .validation import validate as run_validation, message_for

# Kolumny wynikowe raportu CSV (kolejnosc ma znaczenie - tak wyglada plik).
REPORT_COLUMNS = [
    "id",
    "email",
    "imie",
    "nazwisko",
    "result",
    "block_save",
    "block_override_allowed",
    "syntax_valid",
    "domain_status",
    "has_mx",
    "disposable",
    "role_based",
    "suggestion",
    "name_email_match",
    "name_suggestion",
    "duplicate_of_row",
    "message_pl",
]

# Aliasy naglowkow - ludzie eksportuja CRM-y z roznymi nazwami kolumn.
_ALIASES: dict[str, str] = {
    "id": "id", "lp": "id", "nr": "id", "contact_id": "id", "identyfikator": "id",
    "email": "email", "e-mail": "email", "mail": "email", "adres": "email",
    "adres e-mail": "email", "adres email": "email", "email_address": "email",
    "imie": "imie", "imię": "imie", "first_name": "imie", "firstname": "imie",
    "given_name": "imie", "name": "imie",
    "nazwisko": "nazwisko", "last_name": "nazwisko", "lastname": "nazwisko",
    "surname": "nazwisko", "family_name": "nazwisko",
}

_DELIMITERS = [",", ";", "\t", "|"]


def _norm_header(h: str) -> str:
    return (h or "").strip().lstrip("﻿").strip('"').lower()


@dataclass
class InputRow:
    """Jeden wiersz wejsciowy po sparsowaniu CSV."""
    line: int                 # numer wiersza w pliku (1-based, z naglowkiem)
    id: str = ""
    email: str = ""
    imie: str = ""
    nazwisko: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.imie.strip(), self.nazwisko.strip()) if p)


@dataclass
class ParsedCsv:
    rows: list[InputRow] = field(default_factory=list)
    columns: dict[str, int] = field(default_factory=dict)   # kanoniczna -> indeks
    delimiter: str = ","
    skipped_empty: int = 0        # wiersze bez adresu e-mail (pominiete)
    truncated: bool = False       # plik mial wiecej wierszy niz BATCH_MAX_ROWS


class CsvFormatError(ValueError):
    """Plik nie jest CSV z rozpoznawalna kolumna 'email'."""


def _decode(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8-sig", "utf-8", "cp1250", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _sniff_delimiter(sample: str) -> str:
    first = sample.splitlines()[0] if sample.splitlines() else ""
    best, best_count = ",", 0
    for d in _DELIMITERS:
        count = first.count(d)
        if count > best_count:
            best, best_count = d, count
    return best


def _looks_like_email(value: str) -> bool:
    v = (value or "").strip()
    return "@" in v and " " not in v and len(v) >= 3


def _map_columns(header: list[str]) -> dict[str, int]:
    """Naglowek -> mapa kanoniczna_nazwa: indeks (tylko rozpoznane kolumny)."""
    cols: dict[str, int] = {}
    for idx, raw in enumerate(header):
        canon = _ALIASES.get(_norm_header(raw))
        if canon and canon not in cols:
            cols[canon] = idx
    return cols


def parse_csv(raw: bytes | str, *, max_rows: Optional[int] = None) -> ParsedCsv:
    """
    Parsuje CSV (id, email, imie, nazwisko) w sposob wyrozumialy:
      - separator wykrywany automatycznie (`,` `;` tab `|`),
      - kodowanie: UTF-8 (z BOM) / CP1250 / latin-1,
      - naglowek po aliasach (np. first_name, e-mail, surname),
      - brak naglowka -> kolumny pozycyjnie (id, email, imie, nazwisko).
    Wiersze bez adresu sa pomijane i policzone w `skipped_empty`.
    """
    max_rows = config.BATCH_MAX_ROWS if max_rows is None else max_rows
    text = _decode(raw)
    if not text.strip():
        raise CsvFormatError("Plik jest pusty")

    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    records = [r for r in reader if any((c or "").strip() for c in r)]
    if not records:
        raise CsvFormatError("Plik jest pusty")

    header = records[0]
    columns = _map_columns(header)
    body = records[1:]

    if "email" not in columns:
        # Brak naglowka? Sprobuj ukladu pozycyjnego id,email,imie,nazwisko.
        email_idx = next((i for i, c in enumerate(header) if _looks_like_email(c)), None)
        if email_idx is None:
            raise CsvFormatError(
                "Nie znaleziono kolumny 'email'. Oczekiwany naglowek: id,email,imie,nazwisko"
            )
        columns = {"email": email_idx}
        if email_idx == 1:
            columns["id"] = 0
            if len(header) > 2:
                columns["imie"] = 2
            if len(header) > 3:
                columns["nazwisko"] = 3
        body = records  # pierwszy wiersz to dane, nie naglowek

    def cell(rec: list[str], key: str) -> str:
        idx = columns.get(key)
        if idx is None or idx >= len(rec):
            return ""
        return (rec[idx] or "").strip()

    out = ParsedCsv(columns=columns, delimiter=delimiter)
    header_offset = 2 if body is not records else 1
    for i, rec in enumerate(body):
        email = cell(rec, "email")
        if not email:
            out.skipped_empty += 1
            continue
        if len(out.rows) >= max_rows:
            out.truncated = True
            break
        out.rows.append(
            InputRow(
                line=i + header_offset,
                id=cell(rec, "id") or str(len(out.rows) + 1),
                email=email,
                imie=cell(rec, "imie"),
                nazwisko=cell(rec, "nazwisko"),
            )
        )
    return out


def _name_match_for(name: str, email: str) -> Optional[dict]:
    """Heurystyka zgodnosci imie<->email (+ AI, gdy przelacznik wlaczony)."""
    if not name.strip() or not config.NAME_MATCH_ENABLED:
        return None
    result = name_match.evaluate(name, email)
    return ai_client.refine_name_match(name, email, result) or result


def _validate_one(email: str, checks: list[str]) -> dict:
    """Walidacja jednego adresu + polityka blokowania (jak w /validate)."""
    raw = run_validation(email, checks)
    policy = config.resolve_block(raw["result"])
    raw["block_save"] = policy["block_save"]
    raw["block_override_allowed"] = policy["override_allowed"]
    raw["message_pl"] = message_for(
        raw["result"],
        domain=(email.split("@")[-1] if "@" in email else ""),
        suggestion=(raw["suggestion"].split("@")[-1] if raw.get("suggestion") else ""),
    )
    return raw


def validate_rows(
    rows: list[InputRow],
    *,
    checks: Optional[list[str]] = None,
    max_workers: Optional[int] = None,
    with_name_match: bool = True,
) -> list[dict[str, Any]]:
    """
    Waliduje wiersze i zwraca liste dictow zgodnych z REPORT_COLUMNS.

    Powtorzony adres liczymy raz (`duplicate_of_row` wskazuje pierwsze
    wystapienie), ale zgodnosc imie/nazwisko liczymy dla kazdego wiersza -
    ten sam adres moze byc przypisany do roznych osob.
    """
    checks = checks or ["syntax", "typo", "dns", "mx", "lists"]
    workers = max_workers or config.BATCH_MAX_WORKERS

    unique: dict[str, int] = {}     # email(lower) -> indeks pierwszego wiersza
    for i, row in enumerate(rows):
        unique.setdefault(row.email.strip().lower(), i)

    emails = list(unique.keys())
    if workers > 1 and len(emails) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda e: _validate_one(e, checks), emails))
    else:
        results = [_validate_one(e, checks) for e in emails]
    by_email = dict(zip(emails, results))

    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        key = row.email.strip().lower()
        raw = by_email[key]
        first = unique[key]
        nm = _name_match_for(row.full_name, row.email) if (with_name_match and raw["syntax_valid"]) else None
        out.append(
            {
                "id": row.id,
                "email": row.email,
                "imie": row.imie,
                "nazwisko": row.nazwisko,
                "result": raw["result"],
                "block_save": raw["block_save"],
                "block_override_allowed": raw["block_override_allowed"],
                "syntax_valid": raw["syntax_valid"],
                "domain_status": raw["domain_status"],
                "has_mx": raw["has_mx"],
                "disposable": raw["disposable"],
                "role_based": raw["role_based"],
                "suggestion": raw["suggestion"],
                "name_email_match": (nm or {}).get("status"),
                "name_suggestion": (nm or {}).get("suggestion"),
                "duplicate_of_row": None if first == i else rows[first].id,
                "message_pl": raw["message_pl"],
            }
        )
    return out


def summarize(results: list[dict[str, Any]], *, elapsed_ms: int = 0) -> dict[str, Any]:
    """Agregaty do raportu: rozklad wynikow, blokady, flagi, domeny, duplikaty."""
    total = len(results)
    by_result = Counter(r["result"] for r in results)
    by_name_match = Counter(r["name_email_match"] for r in results if r["name_email_match"])
    def _domain(row: dict[str, Any]) -> str:
        return row["email"].split("@")[-1].strip().lower() if "@" in row["email"] else ""

    domains = Counter(d for d in (_domain(r) for r in results) if d)
    problem_domains = Counter(
        d for d, r in ((_domain(r), r) for r in results) if d and r["result"] != "valid"
    )
    blocked = [r for r in results if r["block_save"]]
    return {
        "total_rows": total,
        "unique_emails": len({r["email"].strip().lower() for r in results}),
        "duplicates": sum(1 for r in results if r["duplicate_of_row"] is not None),
        "by_result": dict(by_result.most_common()),
        "valid": by_result.get("valid", 0),
        "blocked": len(blocked),
        "blocked_hard": sum(1 for r in blocked if not r["block_override_allowed"]),
        "blocked_conditional": sum(1 for r in blocked if r["block_override_allowed"]),
        "with_suggestion": sum(1 for r in results if r["suggestion"]),
        "disposable": sum(1 for r in results if r["disposable"]),
        "role_based": sum(1 for r in results if r["role_based"]),
        "by_name_match": dict(by_name_match.most_common()),
        "top_domains": domains.most_common(10),
        "top_problem_domains": problem_domains.most_common(10),
        "quality_score_pct": round(100 * by_result.get("valid", 0) / total, 1) if total else 0.0,
        "elapsed_ms": elapsed_ms,
        "ms_per_row": round(elapsed_ms / total, 1) if total else 0.0,
    }


def run(
    raw: bytes | str,
    *,
    checks: Optional[list[str]] = None,
    max_rows: Optional[int] = None,
    max_workers: Optional[int] = None,
    with_name_match: bool = True,
) -> dict[str, Any]:
    """Pelny przebieg: CSV -> wyniki + podsumowanie (jeden slownik raportu)."""
    parsed = parse_csv(raw, max_rows=max_rows)
    started = time.perf_counter()
    results = validate_rows(
        parsed.rows, checks=checks, max_workers=max_workers, with_name_match=with_name_match
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    summary = summarize(results, elapsed_ms=elapsed_ms)
    summary["skipped_empty"] = parsed.skipped_empty
    summary["truncated"] = parsed.truncated
    summary["max_rows"] = config.BATCH_MAX_ROWS if max_rows is None else max_rows
    summary["checks"] = checks or ["syntax", "typo", "dns", "mx", "lists"]
    return {"summary": summary, "rows": results}


def to_csv(results: list[dict[str, Any]]) -> str:
    """Raport jako CSV (te same kolumny co REPORT_COLUMNS, separator `,`)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in results:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in REPORT_COLUMNS})
    return buf.getvalue()
