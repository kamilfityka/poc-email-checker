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

# --- Kroki walidacji w raporcie ---------------------------------------------
# Te same kroki i te same komunikaty co "Wykonane kroki" w demo formularza
# (static/demo.html) - raport wsadowy ma pokazywac dokladnie to samo, tylko dla
# calej listy naraz. Stany: ok / ostrzezenie / blad / nieustalone / pominieto.
STEPS = ["skladnia", "literowka", "domena_dns", "poczta_mx", "listy", "imie_adres"]

STEP_LABELS = {
    "skladnia": "Skladnia",
    "literowka": "Literowka",
    "domena_dns": "Domena / DNS",
    "poczta_mx": "Poczta / MX",
    "listy": "Listy",
    "imie_adres": "Imie <-> adres",
}

# Stan kroku -> wartosc w kolumnie raportu CSV.
STATE_PL = {
    "ok": "ok",
    "warn": "ostrzezenie",
    "err": "blad",
    "info": "nieustalone",
    "skip": "pominieto",
}

# Kolumny raportu CSV. "simple" to odpowiedz na pytanie "co przeszlo, a co nie" -
# jedna kolumna na krok walidacji. "full" dokłada surowe pola kontraktu §6 dla
# tych, ktorzy chca analizowac dane dalej.
REPORT_COLUMNS_SIMPLE = [
    "id", "email", "imie", "nazwisko",
    *STEPS,
    "wynik", "zapis", "uwagi",
]

REPORT_COLUMNS_FULL = REPORT_COLUMNS_SIMPLE + [
    "suggestion", "domain_status", "has_mx", "disposable", "role_based",
    "name_email_match", "name_suggestion", "name_match_source",
    "duplicate_of_row", "message_pl",
]

# Zachowane dla zgodnosci - domyslny uklad raportu.
REPORT_COLUMNS = REPORT_COLUMNS_SIMPLE

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


def steps_for(row: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Rozklada wynik jednego wiersza na kroki walidacji: co przeszlo, co nie.

    Odpowiednik listy "Wykonane kroki" z demo formularza, tyle ze liczony na
    serwerze dla calej listy. Zwraca {krok: {"state": ..., "text": ...}} dla
    kazdego kroku z STEPS; kroki niewykonane maja state "skip".
    """
    def st(state: str, text: str) -> dict[str, str]:
        return {"state": state, "text": text}

    out: dict[str, dict[str, str]] = {}

    # 1. Skladnia (L0) - jedyny krok, po ktorym reszta sie nie wykonuje.
    if not row["syntax_valid"]:
        out["skladnia"] = st("err", "bledny format - kolejne kroki pominiete")
        for step in STEPS[1:]:
            out[step] = st("skip", "pominieto (bledna skladnia)")
        return out
    out["skladnia"] = st("ok", "poprawny format adresu")

    # 2. Literowka w domenie (L1).
    if row["suggestion"]:
        out["literowka"] = st("warn", f"podejrzenie literowki -> {row['suggestion']}")
    else:
        out["literowka"] = st("ok", "brak literowki w domenie")

    # 3. Istnienie domeny (L2).
    status = row["domain_status"]
    if status == "ok":
        out["domena_dns"] = st("ok", "domena istnieje")
    elif status == "not_found":
        out["domena_dns"] = st("err", "domena nie istnieje (NXDOMAIN)")
    elif status == "unknown":
        out["domena_dns"] = st("info", "niepewny wynik DNS (timeout/SERVFAIL) - nie blokuje")
    else:
        out["domena_dns"] = st("skip", "pominieto (literowka lub warstwa wylaczona)")

    # 4. Obsluga poczty (L3).
    if status != "ok":
        out["poczta_mx"] = st("skip", "pominieto")
    elif row["has_mx"] is True:
        out["poczta_mx"] = st("ok", "domena przyjmuje poczte (rekord MX)")
    elif row["has_mx"] is False:
        out["poczta_mx"] = st("warn", "brak MX (fallback na rekord A lub brak obslugi poczty)")
    else:
        out["poczta_mx"] = st("info", "MX nieustalone - nie blokuje")

    # 5. Listy: jednorazowe / funkcyjne (L4).
    if row["disposable"]:
        out["listy"] = st("warn", "adres jednorazowy (domena tymczasowa)")
    elif row["role_based"]:
        out["listy"] = st("info", "adres funkcyjny (role-based) - tylko sygnal")
    else:
        out["listy"] = st("ok", "brak zastrzezen na listach")

    # 6. Zgodnosc imie/nazwisko <-> adres (heurystyka lub AI).
    match = row.get("name_email_match")
    if not match:
        out["imie_adres"] = st("skip", "nie sprawdzano (brak imienia lub warstwa wylaczona)")
    else:
        src = "AI" if row.get("name_match_source") == "ai" else "heurystyka"
        text, state = {
            "match": ("imie i nazwisko pasuja do adresu", "ok"),
            "partial": ("czesciowa zgodnosc imienia z adresem", "warn"),
            "mismatch": ("imie i nazwisko nie pasuja do adresu", "err"),
            "unknown": ("nie udalo sie ocenic zgodnosci", "info"),
        }.get(match, ("nie udalo sie ocenic zgodnosci", "info"))
        out["imie_adres"] = st(state, f"{text} ({src})")
    return out


def _zapis_label(row: dict[str, Any]) -> str:
    """Slowny odpowiednik block_save - w raporcie ma byc czytelny bez legendy."""
    if not row["block_save"]:
        return "dozwolony"
    return ("zablokowany (mozliwe potwierdzenie reczne)"
            if row["block_override_allowed"] else "zablokowany (twardo)")


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
        entry = {
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
            "name_match_source": (nm or {}).get("source"),
            "duplicate_of_row": None if first == i else rows[first].id,
            "message_pl": raw["message_pl"],
        }
        entry["steps"] = steps_for(entry)
        entry["zapis"] = _zapis_label(entry)
        out.append(entry)
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
    # Rozklad stanow per krok walidacji - serce raportu "co przeszlo, a co nie".
    by_step = {
        step: {
            "ok": 0, "ostrzezenie": 0, "blad": 0, "nieustalone": 0, "pominieto": 0,
        }
        for step in STEPS
    }
    for r in results:
        for step, info in (r.get("steps") or {}).items():
            by_step[step][STATE_PL[info["state"]]] += 1

    return {
        "total_rows": total,
        "by_step": by_step,
        "step_labels": STEP_LABELS,
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


def to_csv(results: list[dict[str, Any]], *, columns: str = "simple") -> str:
    """
    Raport jako CSV. `columns="simple"` (domyslnie) - po jednej kolumnie na krok
    walidacji (ok / ostrzezenie / blad / nieustalone / pominieto), wynik, czy
    zapis jest dozwolony i kolumna `uwagi` z opisem tego, co nie przeszlo.
    `columns="full"` dokłada surowe pola kontraktu §6.
    """
    fields = REPORT_COLUMNS_FULL if columns == "full" else REPORT_COLUMNS_SIMPLE
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in results:
        steps = r.get("steps") or {}
        flat = {
            **r,
            **{step: STATE_PL[info["state"]] for step, info in steps.items()},
            "wynik": r["result"],
            "zapis": r.get("zapis", ""),
            "uwagi": "; ".join(
                f"{STEP_LABELS[step]}: {steps[step]['text']}"
                for step in STEPS
                if step in steps and steps[step]["state"] not in ("ok", "skip")
            ),
        }
        writer.writerow({k: ("" if flat.get(k) is None else flat.get(k)) for k in fields})
    return buf.getvalue()
