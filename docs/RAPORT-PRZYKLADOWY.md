# Raport przykładowy — walidacja wsadowa 1000 adresów

Przebieg kontrolny walidacji wsadowej na przykładowej, celowo „brudnej" bazie
kontaktów (`scripts/przyklad_1000.csv`, 1000 wierszy: `id, email, imie, nazwisko`).
Plik jest generowany z ustalonym ziarnem losowym, więc wynik jest powtarzalny:

```bash
python3 scripts/generuj_przyklad_csv.py --n 1000 -o scripts/przyklad_1000.csv
python3 scripts/waliduj_csv.py scripts/przyklad_1000.csv -o raport.csv --json raport.json
```

Warstwy: `syntax, typo, dns, mx, lists` (komplet) + zgodność imię/nazwisko ↔ adres.
DNS był odpytywany **naprawdę** (nie mock) — czasy poniżej są realne.

## Podsumowanie

| Metryka | Wynik |
|---|---|
| Wierszy w pliku | 1000 |
| Unikalnych adresów | 934 (66 duplikatów) |
| **Poprawnych (`valid`)** | **761 — 76,1 % bazy** |
| Blokada zapisu (`block_save`) | 113 (twarda 62, warunkowa 51) |
| Adresy z sugestią poprawki domeny | 91 |
| Adresy jednorazowe (disposable) | 35 |
| Adresy funkcyjne (`biuro@`, `kontakt@`…) | 53 |
| Czas | **~0,7–0,8 s** dla 1000 wierszy (0,8 ms/wiersz) |

## Kroki walidacji — ile wierszy przeszło, a ile nie

Rdzeń raportu: każdy wiersz jest sprawdzany krok po kroku, tak samo jak w demo
formularza. `pominięto` oznacza, że krok się nie wykonał, bo wcześniejszy przerwał
ścieżkę (np. przy błędzie składni nie ma sensu pytać DNS, a przy podejrzeniu
literówki walidacja zatrzymuje się przed DNS — §6).

| Krok | Kryterium | ok | ostrzeżenie | błąd | nieustalone | pominięto |
|---|---|---:|---:|---:|---:|---:|
| Składnia | poprawny format adresu | 938 | — | **62** | — | — |
| Literówka | brak literówki w domenie | 847 | 91 | — | — | 62 |
| Domena / DNS | domena istnieje | 796 | — | **51** | — | 153 |
| Poczta / MX | domena przyjmuje pocztę (rekord MX) | 791 | 5 | — | — | 204 |
| Listy | brak zastrzeżeń na listach | 850 | 35 | — | 53 | 62 |
| Imię ↔ adres | imię i nazwisko pasują do adresu | 553 | 289 | 92 | 4 | 62 |

Czytanie kolumn: „ostrzeżenie" to defekt, który **nie blokuje** zapisu (literówka
z gotową sugestią, adres jednorazowy, brak MX z fallbackiem na rekord A, częściowa
zgodność imienia), „błąd" to twardy defekt (błędna składnia, NXDOMAIN, jawny
rozjazd imienia), „nieustalone" to brak pewnej odpowiedzi (timeout DNS, adres
funkcyjny jako sam sygnał) — zgodnie z §6 **nigdy nie blokuje**.

## Rozkład wyników

| `result` | Ile | Udział | `block_save` |
|---|---|---|---|
| `valid` | 761 | 76,1 % | nie |
| `typo_suspected` | 91 | 9,1 % | nie (ostrzeżenie + sugestia) |
| `syntax_invalid` | 62 | 6,2 % | **tak, twarda** |
| `domain_not_found` | 51 | 5,1 % | **tak, warunkowa** (checkbox „potwierdzam ręcznie") |
| `disposable` | 35 | 3,5 % | nie (ostrzeżenie) |

Nie wystąpiło `unknown` — DNS odpowiadał stabilnie. W sieci z wolnym resolverem
część domen wypadłaby jako `unknown`, co **nigdy nie blokuje zapisu** (§6).

## Zgodność imię/nazwisko ↔ adres (sygnał informacyjny)

| Status | Ile |
|---|---|
| `match` | 553 |
| `partial` | 289 |
| `mismatch` | 92 |
| `unknown` | 4 |

`mismatch` to głównie adresy funkcyjne (`kontakt@vp.pl` przypisany do osoby) oraz
kontakty, gdzie adres należy ewidentnie do kogoś innego — dobry filtr do przeglądu
ręcznego. Sygnał jest **informacyjny**, nie wpływa na `block_save`.

> Uwaga interpretacyjna: `name_suggestion` proponuje imię/nazwisko odczytane
> z adresu (np. `anna.krawczk@…` + „Anna Krawczyk" → sugestia „Anna Krawczk").
> Przy imporcie bazy zwykle to **adres** jest podejrzany, nie nazwisko — dlatego
> tej kolumny nie traktujemy jako gotowej poprawki, tylko jako wskazanie rozjazdu.

## Domeny generujące najwięcej problemów

| Domena | Problemów | Rodzaj |
|---|---|---|
| stara-nazwa-spolki-2003.pl | 17 | domena nie istnieje |
| xyz-nieznana-domena-7788.eu | 14 | domena nie istnieje |
| outlok.com | 12 | literówka → `outlook.com` |
| guerrillamail.com | 11 | adres jednorazowy |
| firma-ktorej-nie-ma-9812.pl | 11 | domena nie istnieje |
| gazeta.pi | 9 | literówka → `gazeta.pl` |
| nieistniejaca-domena-4471.com | 9 | domena nie istnieje |
| gmai.com | 9 | literówka → `gmail.com` |
| onte.pl | 8 | literówka → `onet.pl` |
| yopmail.com | 8 | adres jednorazowy |

## Przykładowe wiersze raportu

| id | email | osoba | result | block_save | suggestion | imię↔adres |
|---|---|---|---|---|---|---|
| 18 | pawel.zajac62@hotmial.com | Paweł Zając | `typo_suspected` | nie | pawel.zajac62@**hotmail.com** | match |
| 55 | barbaragrabowski..x@icloud.com | Barbara Grabowski | `syntax_invalid` | **twarda** | — | — |
| 62 | znowak@outlook | Zofia Nowak | `syntax_invalid` | **twarda** | — | — |
| 43 | jakub.grabowski@stara-nazwa-spolki-2003.pl | Jakub Grabowski | `domain_not_found` | warunkowa | — | match |
| 11 | tomasz79@mailinator.com | Tomasz Adamczyk | `disposable` | nie | — | partial |
| 26 | kontakt@vp.pl | Mateusz Szymański | `valid` (role) | nie | — | mismatch |
| 19 | karolina.nowak54@outlook.com | Agnieszka Mazur | `valid` | nie | — | mismatch |
| 14 | aleksandra.dabrowsi@orange.pl | Aleksandra Dąbrowski | `valid` | nie | — | duplikat wiersza 2 |

Tak wygląda to w pliku CSV (układ domyślny, `columns=simple`):

```csv
id,email,imie,nazwisko,skladnia,literowka,domena_dns,poczta_mx,listy,imie_adres,wynik,zapis,uwagi
3,katarzynawieczorek@protonmail.com,Katarzyna,Wieczorek,ok,ok,ok,ok,ok,ok,valid,dozwolony,
18,pawel.zajac62@hotmial.com,Pawel,Zajac,ok,ostrzezenie,pominieto,pominieto,ok,ok,typo_suspected,dozwolony,Literowka: podejrzenie literowki -> pawel.zajac62@hotmail.com
55,barbaragrabowski..x@icloud.com,Barbara,Grabowski,blad,pominieto,pominieto,pominieto,pominieto,pominieto,syntax_invalid,zablokowany (twardo),Skladnia: bledny format - kolejne kroki pominiete
```

## Wnioski operacyjne

1. **Do natychmiastowej poprawy: 91 literówek w domenie** — serwis podaje gotowy,
   poprawiony adres, więc to najtańsza akcja naprawcza w całej bazie.
2. **62 adresy nie do uratowania automatycznie** (`syntax_invalid`) — wymagają
   kontaktu z klientem albo usunięcia; przy zapisie w formularzu byłyby zablokowane twardo.
3. **51 adresów z nieistniejącą domeną** — typowo dawne domeny firmowe po zmianie nazwy;
   blokada warunkowa, czyli operator może świadomie potwierdzić zapis.
4. **35 adresów jednorazowych + 53 funkcyjne** — nie są błędne, ale nie nadają się do
   komunikacji 1:1 (warto oflagować w CRM osobnym atrybutem).
5. **66 duplikatów** — do scalenia rekordów; raport wskazuje `id` pierwszego wystąpienia.
6. **Wydajność:** 1000 wierszy w ~0,8 s przy pełnym DNS. Cache per-domena + walidacja
   unikalnych adresów sprawiają, że wąskim gardłem jest liczba **domen**, nie adresów —
   baza 50 tys. kontaktów z tych samych domen zajmie kilkanaście sekund.
