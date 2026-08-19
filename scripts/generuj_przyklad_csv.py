#!/usr/bin/env python3
"""
Generator przykladowej bazy kontaktow (id, email, imie, nazwisko) do testu wsadowego.

Tworzy realistyczna "brudna" baze CRM: wiekszosc adresow poprawnych, a reszta to
typowe defekty spotykane w praktyce - literowki w domenie, bledy skladni, domeny
nieistniejace, adresy jednorazowe, funkcyjne (biuro@), duplikaty i rozjazdy
imienia/nazwiska z adresem. Ziarno losowe jest stale, wiec plik jest powtarzalny.

Uzycie:
    python scripts/generuj_przyklad_csv.py --n 1000 -o scripts/przyklad_1000.csv
"""
import argparse
import csv
import random
import unicodedata

IMIONA = [
    "Anna", "Katarzyna", "Małgorzata", "Agnieszka", "Barbara", "Magdalena", "Ewa",
    "Joanna", "Zofia", "Monika", "Aleksandra", "Natalia", "Julia", "Karolina",
    "Piotr", "Krzysztof", "Andrzej", "Tomasz", "Paweł", "Michał", "Marcin",
    "Jakub", "Łukasz", "Kamil", "Grzegorz", "Adam", "Mateusz", "Rafał", "Wojciech",
]
NAZWISKA = [
    "Nowak", "Kowalski", "Wiśniewski", "Wójcik", "Kowalczyk", "Kamiński", "Lewandowski",
    "Zieliński", "Szymański", "Woźniak", "Dąbrowski", "Kozłowski", "Jankowski",
    "Mazur", "Kwiatkowski", "Krawczyk", "Piotrowski", "Grabowski", "Nowicki",
    "Pawłowski", "Michalski", "Adamczyk", "Dudek", "Zając", "Wieczorek", "Fityka",
]
DOMENY_OK = [
    "gmail.com", "wp.pl", "o2.pl", "interia.pl", "onet.pl", "gazeta.pl", "op.pl",
    "outlook.com", "hotmail.com", "icloud.com", "poczta.onet.pl", "protonmail.com",
    "allegro.pl", "orange.pl", "vp.pl",
]
DOMENY_LITEROWKA = [
    "gmial.com", "gmail.co", "gmai.com", "gmail.cm", "wp.pI", "wp.pll", "o2.p",
    "interia.pI", "onet.pI", "onte.pl", "outlok.com", "hotmial.com", "gazeta.pI",
]
DOMENY_NIEISTNIEJACE = [
    "firma-ktorej-nie-ma-9812.pl", "nieistniejaca-domena-4471.com",
    "stara-nazwa-spolki-2003.pl", "xyz-nieznana-domena-7788.eu",
]
DOMENY_JEDNORAZOWE = [
    "mailinator.com", "yopmail.com", "10minutemail.com", "guerrillamail.com",
    "temp-mail.org", "trashmail.com", "sharklasers.com",
]
ROLE = ["biuro", "kontakt", "info", "sekretariat", "sprzedaz", "bok", "admin", "hr"]

_PL = str.maketrans({"ł": "l", "Ł": "L"})


def ascii_low(s: str) -> str:
    s = s.translate(_PL)
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).lower()


def local_part(rng: random.Random, imie: str, nazwisko: str) -> str:
    i, n = ascii_low(imie), ascii_low(nazwisko)
    return rng.choice([
        f"{i}.{n}", f"{i}{n}", f"{i[0]}{n}", f"{n}.{i}", f"{i}_{n}",
        f"{i}.{n}{rng.randint(1, 99)}", f"{i}{rng.randint(60, 99)}",
    ])


def zepsuj_skladnie(rng: random.Random, adres: str) -> str:
    local, _, domain = adres.partition("@")
    return rng.choice([
        f"{local}{domain}",                # brak @
        f"{local}@@{domain}",              # podwojna malpa
        f"{local}..x@{domain}",            # podwojna kropka
        f"{local} {rng.choice('ab')}@{domain}",  # spacja w srodku
        f"{local}@{domain.split('.')[0]}",       # domena bez TLD
        f"{local}@",                       # urwany adres
        f"@{domain}",                      # brak czesci lokalnej
        f"{local}@{domain},",              # przecinek na koncu
    ])


def generuj(n: int, seed: int = 20260819) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []

    # Rozklad defektow ~ jak w typowej, nieczyszczonej bazie CRM.
    plan = (
        ["ok"] * round(n * 0.60)
        + ["literowka"] * round(n * 0.08)
        + ["skladnia"] * round(n * 0.06)
        + ["nieistniejaca"] * round(n * 0.05)
        + ["jednorazowa"] * round(n * 0.04)
        + ["role"] * round(n * 0.05)
        + ["rozjazd_nazwiska"] * round(n * 0.07)
        + ["duplikat"] * round(n * 0.05)
    )
    plan += ["ok"] * (n - len(plan))
    plan = plan[:n]
    rng.shuffle(plan)

    for idx, rodzaj in enumerate(plan, start=1):
        imie = rng.choice(IMIONA)
        nazwisko = rng.choice(NAZWISKA)
        lp = local_part(rng, imie, nazwisko)

        if rodzaj == "ok":
            email = f"{lp}@{rng.choice(DOMENY_OK)}"
        elif rodzaj == "literowka":
            email = f"{lp}@{rng.choice(DOMENY_LITEROWKA)}"
        elif rodzaj == "skladnia":
            email = zepsuj_skladnie(rng, f"{lp}@{rng.choice(DOMENY_OK)}")
        elif rodzaj == "nieistniejaca":
            email = f"{lp}@{rng.choice(DOMENY_NIEISTNIEJACE)}"
        elif rodzaj == "jednorazowa":
            email = f"{lp}@{rng.choice(DOMENY_JEDNORAZOWE)}"
        elif rodzaj == "role":
            email = f"{rng.choice(ROLE)}@{rng.choice(DOMENY_OK)}"
        elif rodzaj == "rozjazd_nazwiska":
            # Adres nalezy do zupelnie innej osoby albo nazwisko ma literowke.
            if rng.random() < 0.5:
                inne_n = rng.choice([x for x in NAZWISKA if x != nazwisko])
                email = f"{local_part(rng, rng.choice(IMIONA), inne_n)}@{rng.choice(DOMENY_OK)}"
            else:
                n_ascii = ascii_low(nazwisko)
                pos = rng.randrange(1, max(2, len(n_ascii) - 1))
                zepsute = n_ascii[:pos] + n_ascii[pos + 1:]
                email = f"{ascii_low(imie)}.{zepsute}@{rng.choice(DOMENY_OK)}"
        else:  # duplikat - ten sam adres co wczesniejszy wiersz
            if rows:
                zrodlo = rng.choice(rows)
                email, imie, nazwisko = zrodlo["email"], zrodlo["imie"], zrodlo["nazwisko"]
            else:
                email = f"{lp}@{rng.choice(DOMENY_OK)}"

        rows.append({"id": idx, "email": email, "imie": imie, "nazwisko": nazwisko})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Generuje przykladowa baze kontaktow do testu wsadowego")
    ap.add_argument("--n", type=int, default=1000, help="liczba wierszy (domyslnie 1000)")
    ap.add_argument("--seed", type=int, default=20260819, help="ziarno losowe (powtarzalnosc)")
    ap.add_argument("-o", "--out", default="scripts/przyklad_1000.csv", help="plik wyjsciowy")
    args = ap.parse_args()

    rows = generuj(args.n, args.seed)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "email", "imie", "nazwisko"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Zapisano {len(rows)} wierszy -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
