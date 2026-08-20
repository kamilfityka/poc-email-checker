#!/usr/bin/env python3
"""
Walidacja wsadowa z linii polecen: CSV (id, email, imie, nazwisko) -> raport.

Uzywa dokladnie tego samego rdzenia co serwis (`app/batch.py`), wiec wyniki sa
identyczne jak z `POST /validate/csv` - roznica jest tylko taka, ze tu nie ma
limitu czasu HTTP, wiec wygodniej puscic wieksza baze (np. 50k adresow) z crona.

Uzycie:
    python scripts/waliduj_csv.py kontakty.csv
    python scripts/waliduj_csv.py kontakty.csv -o raport.csv --json raport.json
    python scripts/waliduj_csv.py kontakty.csv --checks syntax,typo,lists   # bez DNS
    python scripts/waliduj_csv.py kontakty.csv --workers 16 --max-rows 100000
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import batch  # noqa: E402


def _bar(count: int, total: int, width: int = 28) -> str:
    filled = round(width * count / total) if total else 0
    return "#" * filled + "." * (width - filled)


def print_summary(summary: dict) -> None:
    total = summary["total_rows"]
    print()
    print("=" * 62)
    print(f"  RAPORT WALIDACJI - {total} wierszy "
          f"({summary['unique_emails']} unikalnych adresow)")
    print("=" * 62)
    print(f"  Czas: {summary['elapsed_ms']} ms  ({summary['ms_per_row']} ms/wiersz)")
    print(f"  Warstwy: {', '.join(summary['checks'])}")
    if summary.get("skipped_empty"):
        print(f"  Pominieto wierszy bez adresu: {summary['skipped_empty']}")
    if summary.get("truncated"):
        print(f"  UWAGA: plik obciety do limitu {summary['max_rows']} wierszy")
    print()
    print("  Kroki walidacji - ile wierszy przeszlo, a ile nie:")
    print(f"    {'krok':<16}{'ok':>7}{'ostrzez.':>10}{'blad':>7}{'nieust.':>9}{'pominieto':>11}")
    for step, counts in summary["by_step"].items():
        print(f"    {summary['step_labels'][step]:<16}"
              f"{counts['ok']:>7}{counts['ostrzezenie']:>10}{counts['blad']:>7}"
              f"{counts['nieustalone']:>9}{counts['pominieto']:>11}")
    print()
    print("  Rozklad wynikow:")
    for result, count in summary["by_result"].items():
        pct = 100 * count / total if total else 0
        print(f"    {result:<20} {count:>6}  {pct:>5.1f}%  {_bar(count, total)}")
    print()
    print(f"  Poprawne (valid):            {summary['valid']} "
          f"({summary['quality_score_pct']}% bazy)")
    print(f"  Blokada zapisu (block_save): {summary['blocked']}"
          f"  [twarda: {summary['blocked_hard']}, warunkowa: {summary['blocked_conditional']}]")
    print(f"  Sugestia poprawki domeny:    {summary['with_suggestion']}")
    print(f"  Adresy jednorazowe:          {summary['disposable']}")
    print(f"  Adresy funkcyjne (role):     {summary['role_based']}")
    print(f"  Duplikaty adresu:            {summary['duplicates']}")
    if summary.get("by_name_match"):
        print()
        print("  Zgodnosc imie/nazwisko <-> adres:")
        for status, count in summary["by_name_match"].items():
            print(f"    {status:<20} {count:>6}")
    if summary.get("top_problem_domains"):
        print()
        print("  Domeny z najwieksza liczba problemow:")
        for domain, count in summary["top_problem_domains"]:
            print(f"    {domain:<28} {count:>6}")
    print("=" * 62)


def main() -> int:
    ap = argparse.ArgumentParser(description="Walidacja wsadowa adresow e-mail z pliku CSV")
    ap.add_argument("plik", help="wejsciowy CSV (kolumny: id, email, imie, nazwisko)")
    ap.add_argument("-o", "--out", default="raport-walidacji.csv", help="wyjsciowy raport CSV")
    ap.add_argument("--json", dest="json_out", default=None, help="dodatkowo raport JSON")
    ap.add_argument("--checks", default=None,
                    help="warstwy po przecinku (syntax,typo,dns,mx,lists); domyslnie wszystkie")
    ap.add_argument("--workers", type=int, default=None, help="rownoleglosc zapytan DNS")
    ap.add_argument("--max-rows", type=int, default=None, help="limit wierszy")
    ap.add_argument("--no-name-match", action="store_true",
                    help="pomin zgodnosc imie/nazwisko <-> adres")
    ap.add_argument("--pelny", action="store_true",
                    help="raport CSV z surowymi polami kontraktu (domyslnie: same kroki walidacji)")
    args = ap.parse_args()

    path = Path(args.plik)
    if not path.exists():
        print(f"Nie ma pliku: {path}", file=sys.stderr)
        return 2

    checks = [c.strip() for c in args.checks.split(",") if c.strip()] if args.checks else None
    started = time.perf_counter()
    try:
        report = batch.run(
            path.read_bytes(),
            checks=checks,
            max_rows=args.max_rows,
            max_workers=args.workers,
            with_name_match=not args.no_name_match,
        )
    except batch.CsvFormatError as exc:
        print(f"Blad pliku CSV: {exc}", file=sys.stderr)
        return 2

    Path(args.out).write_text(
        "\ufeff" + batch.to_csv(report["rows"], columns="full" if args.pelny else "simple"),
        encoding="utf-8",
    )
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print_summary(report["summary"])
    print(f"  Raport CSV: {args.out}")
    if args.json_out:
        print(f"  Raport JSON: {args.json_out}")
    print(f"  Calkowity czas z I/O: {round((time.perf_counter() - started) * 1000)} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
