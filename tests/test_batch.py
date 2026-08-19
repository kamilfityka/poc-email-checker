"""
Testy walidacji wsadowej (CSV -> raport).

DNS jest mockowany per-domena, zeby testy byly szybkie i offline. Sprawdzamy:
  - parsowanie roznych wariantow CSV (separatory, aliasy kolumn, brak naglowka),
  - zgodnosc wynikow batcha z pojedynczym /validate (ta sama polityka blokowania),
  - deduplikacje powtorzonych adresow,
  - agregaty raportu i format CSV,
  - kontrakt endpointu POST /validate/csv (json + csv, bledy 400/413).
"""
import io

import pytest
from fastapi.testclient import TestClient

from app import batch, config, validation
from app.main import app

client = TestClient(app)

CSV_1 = (
    "id,email,imie,nazwisko\n"
    "1,jan.kowalski@wp.pl,Jan,Kowalski\n"          # valid
    "2,anna.nowak@gmial.com,Anna,Nowak\n"          # literowka -> typo_suspected
    "3,zla@@nazwa,Zla,Nazwa\n"                     # syntax_invalid
    "4,foo@mailinator.com,Foo,Bar\n"               # disposable
    "5,biuro@wp.pl,Biuro,Firmy\n"                  # role_based (valid)
    "6,JAN.KOWALSKI@wp.pl,Jan,Kowalski\n"          # duplikat wiersza 1
)


@pytest.fixture(autouse=True)
def _dns_ok(monkeypatch):
    """Kazda domena istnieje i ma MX - defekty w testach pochodza z L0/L1/L4."""
    monkeypatch.setattr(
        validation, "check_dns_mx",
        lambda domain: {"domain_status": "ok", "has_mx": True, "has_a": True, "cached": False},
    )


# --- parsowanie CSV ----------------------------------------------------------
def test_parse_basic():
    parsed = batch.parse_csv(CSV_1)
    assert len(parsed.rows) == 6
    assert parsed.rows[0].email == "jan.kowalski@wp.pl"
    assert parsed.rows[0].full_name == "Jan Kowalski"
    assert parsed.delimiter == ","


def test_parse_semicolon_and_aliases():
    raw = "Lp;E-mail;First_name;Surname\n1;a.b@wp.pl;Adam;Bak\n"
    parsed = batch.parse_csv(raw)
    assert parsed.delimiter == ";"
    assert parsed.rows[0].email == "a.b@wp.pl"
    assert parsed.rows[0].imie == "Adam" and parsed.rows[0].nazwisko == "Bak"


def test_parse_bom_and_utf8_bytes():
    raw = "﻿id,email,imie,nazwisko\n7,ewa@wp.pl,Ewa,Zając\n".encode("utf-8")
    parsed = batch.parse_csv(raw)
    assert parsed.rows[0].id == "7"
    assert parsed.rows[0].nazwisko == "Zając"


def test_parse_without_header_positional():
    parsed = batch.parse_csv("1,jan@wp.pl,Jan,Kowalski\n2,ewa@wp.pl,Ewa,Nowak\n")
    assert len(parsed.rows) == 2
    assert parsed.rows[1].imie == "Ewa"


def test_parse_skips_rows_without_email():
    parsed = batch.parse_csv("id,email,imie,nazwisko\n1,,Jan,Kowalski\n2,ewa@wp.pl,Ewa,Nowak\n")
    assert len(parsed.rows) == 1 and parsed.skipped_empty == 1


def test_parse_respects_max_rows():
    rows = "id,email\n" + "".join(f"{i},u{i}@wp.pl\n" for i in range(50))
    parsed = batch.parse_csv(rows, max_rows=10)
    assert len(parsed.rows) == 10 and parsed.truncated is True


def test_parse_rejects_file_without_email_column():
    with pytest.raises(batch.CsvFormatError):
        batch.parse_csv("kolumna,inna\nwartosc,druga\n")


def test_parse_rejects_empty_file():
    with pytest.raises(batch.CsvFormatError):
        batch.parse_csv("   ")


# --- walidacja wierszy -------------------------------------------------------
def test_results_match_single_validate_policy():
    report = batch.run(CSV_1)
    by_id = {r["id"]: r for r in report["rows"]}
    assert by_id["1"]["result"] == "valid"
    assert by_id["2"]["result"] == "typo_suspected"
    assert by_id["2"]["suggestion"] == "anna.nowak@gmail.com"
    assert by_id["3"]["result"] == "syntax_invalid"
    assert by_id["3"]["block_save"] is True and by_id["3"]["block_override_allowed"] is False
    assert by_id["4"]["result"] == "disposable" and by_id["4"]["disposable"] is True
    assert by_id["5"]["result"] == "valid" and by_id["5"]["role_based"] is True


def test_batch_row_equals_validate_endpoint():
    """Batch nie moze dawac innego werdyktu niz /validate dla tego samego adresu."""
    rows = batch.run("id,email\n1,anna.nowak@gmial.com\n")["rows"]
    single = client.post("/validate", json={"email": "anna.nowak@gmial.com"}).json()
    for field in ("result", "block_save", "block_override_allowed", "syntax_valid",
                  "disposable", "role_based", "suggestion", "message_pl"):
        assert rows[0][field] == single[field], field


def test_duplicates_are_marked_and_counted():
    report = batch.run(CSV_1)
    dup = [r for r in report["rows"] if r["duplicate_of_row"]]
    assert len(dup) == 1 and dup[0]["id"] == "6" and dup[0]["duplicate_of_row"] == "1"
    assert report["summary"]["duplicates"] == 1
    assert report["summary"]["unique_emails"] == 5


def test_duplicate_reuses_validation_but_keeps_own_name_match():
    """Ten sam adres moze byc przypisany do innej osoby - zgodnosc liczymy per wiersz."""
    raw = ("id,email,imie,nazwisko\n"
           "1,jan.kowalski@wp.pl,Jan,Kowalski\n"
           "2,jan.kowalski@wp.pl,Barbara,Zielinska\n")
    rows = batch.run(raw)["rows"]
    assert rows[0]["result"] == rows[1]["result"] == "valid"
    assert rows[0]["name_email_match"] == "match"
    assert rows[1]["name_email_match"] == "mismatch"


def test_checks_subset_skips_dns_and_lists():
    rows = batch.run("id,email\n1,foo@mailinator.com\n", checks=["syntax"])["rows"]
    assert rows[0]["result"] == "valid"          # bez 'lists' nie wykryjemy disposable
    assert rows[0]["domain_status"] == "not_checked"


def test_name_match_can_be_disabled():
    rows = batch.run(CSV_1, with_name_match=False)["rows"]
    assert all(r["name_email_match"] is None for r in rows)


# --- podsumowanie + CSV ------------------------------------------------------
def test_summary_aggregates():
    s = batch.run(CSV_1)["summary"]
    assert s["total_rows"] == 6
    assert s["by_result"]["valid"] == 3            # wiersze 1, 5, 6 (duplikat)
    assert s["valid"] == 3 and s["quality_score_pct"] == 50.0
    assert s["blocked"] == 1 and s["blocked_hard"] == 1
    assert s["with_suggestion"] == 1
    assert s["disposable"] == 1 and s["role_based"] == 1
    assert s["checks"] == ["syntax", "typo", "dns", "mx", "lists"]
    assert ("wp.pl", 3) in s["top_domains"]
    # Wiersz z bledem skladni nie ma sensownej domeny - nie zasmiecamy statystyk.
    assert all(domain for domain, _ in s["top_problem_domains"])


def test_to_csv_has_all_columns_and_rows():
    report = batch.run(CSV_1)
    text = batch.to_csv(report["rows"])
    lines = text.strip().splitlines()
    assert lines[0].split(",") == batch.REPORT_COLUMNS
    assert len(lines) == 7                        # naglowek + 6 wierszy
    assert "None" not in text                     # None -> puste pole


# --- endpoint ----------------------------------------------------------------
def _upload(content: str, params: str = "", filename: str = "kontakty.csv"):
    return client.post(
        f"/validate/csv{params}",
        files={"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")},
    )


def test_endpoint_json_report():
    res = _upload(CSV_1)
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["total_rows"] == 6
    assert len(body["rows"]) == 6
    assert body["rows"][0]["email"] == "jan.kowalski@wp.pl"


def test_endpoint_csv_report_is_downloadable():
    res = _upload(CSV_1, "?format=csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert res.text.startswith("id,email,imie,nazwisko,result")


def test_endpoint_checks_param():
    body = _upload("id,email\n1,foo@mailinator.com\n", "?checks=syntax").json()
    assert body["rows"][0]["result"] == "valid"
    assert body["summary"]["checks"] == ["syntax"]


def test_endpoint_rejects_unknown_check():
    assert _upload(CSV_1, "?checks=syntax,teleport").status_code == 400


def test_endpoint_rejects_broken_csv():
    res = _upload("cos,zupelnie\ninnego,pliku\n")
    assert res.status_code == 400
    assert "email" in res.json()["detail"]


def test_endpoint_rejects_too_big_file(monkeypatch):
    monkeypatch.setattr(config, "BATCH_MAX_BYTES", 10)
    assert _upload(CSV_1).status_code == 413


def test_endpoint_respects_max_rows(monkeypatch):
    monkeypatch.setattr(config, "BATCH_MAX_ROWS", 2)
    body = _upload(CSV_1).json()
    assert body["summary"]["total_rows"] == 2
    assert body["summary"]["truncated"] is True


def test_batch_page_served():
    res = client.get("/batch")
    assert res.status_code == 200 and "text/html" in res.headers["content-type"]
