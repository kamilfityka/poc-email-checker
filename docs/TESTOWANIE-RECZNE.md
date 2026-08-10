# Testowanie ręczne — walidator e-mail

Zestaw gotowych przypadków do ręcznego sprawdzenia `POST /validate` oraz panelu
`/admin`. Każdy przypadek ma: **cel → komendę `curl` → oczekiwany wynik**.

> **Wskazówka o determinizmie.** Wyniki zależne od DNS (`domain_not_found`,
> `no_mail_capability`, `unknown`) są uzależnione od realnej sieci i mogą się
> różnić. Najpewniejszym „sterem" jest pole **`checks`** — wybierając, które
> warstwy uruchomić (`syntax`, `typo`, `dns`, `mx`, `lists`), wymuszasz konkretną
> ścieżkę niezależnie od DNS. Przy każdym przypadku pokazuję dobór `checks`.

---

## 0. Uruchomienie

```bash
# Docker (zalecane):
docker compose up -d --build
# albo lokalnie:
uvicorn app.main:app --reload
```

Sprawdź, że żyje:

```bash
curl -s http://localhost:8000/healthz          # -> ok
curl -s http://localhost:8000/config | jq .    # aktywna polityka + przełączniki
```

Ładniejsze wyniki: dopisz `| jq .` do każdego `curl` (jeśli masz `jq`).

**Kontrakt `POST /validate`** (body JSON):
- `email` (wymagane)
- `checks` (opcjonalne, domyślnie `["syntax","typo","dns","mx","lists"]`)
- `name` (opcjonalne — włącza sprawdzenie zgodności imię↔adres)

Kluczowe pola odpowiedzi: `result`, `block_save`, `block_override_allowed`,
`syntax_valid`, `domain_status`, `has_mx`, `disposable`, `role_based`,
`suggestion`, `message_pl`, `name_email_match`, `name_match_source`.

---

## 1. Szybka ściąga (wszystkie kody `result`)

| # | Cel (`result`) | `email` | `checks` | `block_save` |
|---|---|---|---|---|
| 1 | `valid` | `jan.kowalski@gmail.com` | pełne | false |
| 2 | `syntax_invalid` | `zla@@nazwa` | `syntax` | **true (hard)** |
| 3 | `typo_suspected` | `jan@gmial.com` | `syntax,typo` | false (warn) |
| 4 | `domain_not_found` | `jan@nie-istnieje-xyz123.pl` | `syntax,dns,mx` | **true (conditional)** |
| 5 | `no_mail_capability` | domena z A bez MX | `syntax,dns,mx` | **true (conditional)** |
| 6 | `disposable` | `jan@0-mail.com` | `syntax,lists` | false (warn) |
| 7 | `unknown` | zależne od DNS | `syntax,dns,mx` | false (none) |
| 8 | flaga `role_based` | `biuro@gmail.com` | pełne | false |

---

## 2. Przypadki poprawne (`valid`)

```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan.kowalski@gmail.com"}' | jq .
```
**Oczekiwane:** `result: "valid"`, `block_save: false`, `syntax_valid: true`,
`domain_status: "ok"`, `has_mx: true`.

Wariant minimalny (`a@b.co` — poprawny składniowo):
```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"a@b.co","checks":["syntax"]}' | jq .
```
**Oczekiwane:** `result: "valid"` (sama składnia, bez DNS).

---

## 3. Błędna składnia (`syntax_invalid`) — jedyny TWARDY blok

Wszystkie poniższe → `result: "syntax_invalid"`, `block_save: true`,
`block_override_allowed: false` (nie da się „potwierdzić ręcznie").

```bash
for e in "zla@@nazwa" "bez-malpy.pl" "@wp.pl" "spacja @wp.pl" "kropka.@wp.pl" "brak@domeny"; do
  echo "== $e =="
  curl -s -X POST http://localhost:8000/validate \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$e\",\"checks\":[\"syntax\"]}" | jq -c '{result,block_save,block_override_allowed}'
done
```

### Edge case: pusty / null / same spacje
```bash
curl -s -X POST http://localhost:8000/validate -H "Content-Type: application/json" -d '{"email":""}' | jq -c '{result}'
curl -s -X POST http://localhost:8000/validate -H "Content-Type: application/json" -d '{"email":"   "}' | jq -c '{result}'
```
**Oczekiwane:** oba `syntax_invalid` (nie wywala serwera).

### Edge case: spacje wokół adresu są obcinane
```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"  jan.kowalski@gmail.com  "}' | jq -c '{email,result}'
```
**Oczekiwane:** `email` bez spacji, `result: "valid"`.

---

## 4. Literówka domeny (`typo_suspected`)

Warstwa `typo` nie potrzebuje DNS — działa na słowniku popularnych domen.

```bash
for e in "jan@gmial.com" "jan@gamil.com" "jan@interia.pll" "jan@wp.p"; do
  echo "== $e =="
  curl -s -X POST http://localhost:8000/validate \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$e\",\"checks\":[\"syntax\",\"typo\"]}" | jq -c '{result,suggestion}'
done
```
**Oczekiwane:** `result: "typo_suspected"`, `suggestion` z poprawioną domeną
(`jan@gmail.com`, `jan@interia.pl`, `jan@wp.pl`). `block_save: false` (warn).

### Edge case: wielkie litery → sugestia małymi
```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"Jan@GMIAL.COM","checks":["syntax","typo"]}' | jq -c '{result,suggestion}'
```
**Oczekiwane:** `suggestion: "jan@gmail.com"`.

### Edge case: bez `typo` w `checks` literówka NIE jest wykrywana
```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan@gmial.com","checks":["syntax","dns","mx"]}' | jq -c '{result,suggestion}'
```
**Oczekiwane:** `suggestion: null`, a `result` zależy od DNS dla `gmial.com`.

### Edge case: domena firmowa daleko od słownika → brak „poprawiania na siłę"
```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan@moja-firma.pl","checks":["syntax","typo"]}' | jq -c '{result,suggestion}'
```
**Oczekiwane:** `suggestion: null` (nie `typo_suspected`).

---

## 5. Domena nie istnieje (`domain_not_found`)

Wymaga realnego DNS (NXDOMAIN). `block_save: true`, ale
`block_override_allowed: true` (conditional — można potwierdzić ręcznie).

```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan@ta-domena-na-pewno-nie-istnieje-xyz123.pl","checks":["syntax","dns","mx"]}' \
  | jq -c '{result,domain_status,block_save,block_override_allowed}'
```
**Oczekiwane:** `result: "domain_not_found"`, `domain_status: "not_found"`.

---

## 6. Domena bez obsługi poczty (`no_mail_capability`)

Domena ma rekord A/AAAA, ale **brak MX**. Trudne do zagwarantowania na realnym
DNS (zależy od konkretnej domeny) — jeśli znasz taką w swojej sieci, użyj jej:

```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan@TWOJA-DOMENA-BEZ-MX.pl","checks":["syntax","dns","mx"]}' \
  | jq -c '{result,domain_status,has_mx}'
```
**Oczekiwane:** `result: "no_mail_capability"` (gdy `has_a=true`, `has_mx=false`).
`block_save: true`, override dozwolony.

---

## 7. Adres jednorazowy (`disposable`)

Aby wynik był **deterministyczny**, pomiń DNS (domena z listy może już nie
istnieć — wtedy `domain_not_found` miałby priorytet). Użyj `checks` bez `dns/mx`:

```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan@0-mail.com","checks":["syntax","lists"]}' | jq -c '{result,disposable}'
```
**Oczekiwane:** `result: "disposable"`, `disposable: true`, `block_save: false` (warn).

### Edge case: bez `lists` w `checks` — jednorazowa przechodzi jako `valid`
```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan@0-mail.com","checks":["syntax"]}' | jq -c '{result,disposable}'
```
**Oczekiwane:** `result: "valid"`, `disposable: false`.

---

## 8. Adres funkcyjny (flaga `role_based`)

`role_based` to **tylko sygnał informacyjny** — nie zmienia `result` ani blokady.

```bash
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"biuro@gmail.com","checks":["syntax","lists"]}' | jq -c '{result,role_based,block_save}'
```
**Oczekiwane:** `role_based: true`, `result: "valid"`, `block_save: false`.
(Inne funkcyjne: `info`, `kontakt`, `admin`, `office`, `bok`, `sekretariat`.)

---

## 9. Priorytet warstw (co „wygrywa")

Kolejność: `syntax_invalid` > `typo_suspected` > `domain_not_found` >
`no_mail_capability` > `disposable` > `unknown` > `valid`.

```bash
# Zła składnia bije literówkę (brak sugestii mimo "gmial"):
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"zla@@gmial.com","checks":["syntax","typo"]}' | jq -c '{result,suggestion}'
# -> syntax_invalid, suggestion: null

# Literówka bije DNS (domena idzie na skróty, DNS się nie wykonuje):
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan@gmial.com","checks":["syntax","typo","dns","mx"]}' | jq -c '{result,domain_status}'
# -> typo_suspected, domain_status: "not_checked"
```

---

## 10. Zgodność imię/nazwisko ↔ adres (heurystyka)

Podaj pole `name`. To sygnał informacyjny — **nie blokuje** zapisu.

```bash
# Zgodność:
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan.kowalski@gmail.com","name":"Jan Kowalski","checks":["syntax"]}' \
  | jq -c '{name_email_match,name_match_source,block_save}'
# -> name_email_match: "match", name_match_source: "heuristic"

# Rozbieżność:
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan.kowalski@gmail.com","name":"Anna Nowak","checks":["syntax"]}' \
  | jq -c '{name_email_match,name_match_source}'
# -> name_email_match: "mismatch" (lub "partial")
```

---

## 11. Warstwa AI (własny model — opcjonalna)

Domyślnie wyłączona. Aby przetestować z prawdziwym LLM: uzupełnij w `.env`
`AI_BASE_URL` / `AI_MODEL` / `AI_API_KEY`, a warstwę włącz przełącznikiem:

```bash
# Włącz AI w locie (runtime toggle):
curl -s -X POST http://localhost:8000/admin/settings \
  -H "Content-Type: application/json" -d '{"ai":true}' | jq -c '.integrations.ai'

# Teraz sprawdzenie name-match użyje modelu (source: "ai"):
curl -s -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"email":"jan.kowalski@gmail.com","name":"Janek Kowaslki","checks":["syntax"]}' \
  | jq -c '{name_email_match,name_suggestion,name_match_source}'
# -> name_match_source: "ai" gdy model odpowie; przy błędzie zostaje "heuristic"
```

> Samo połączenie z LLM sprawdzisz skryptem: `python scripts/test_ai.py`
> (w kontenerze: `docker compose exec validator python scripts/test_ai.py`).

Jeśli `ADMIN_TOKEN` jest ustawiony w `.env`, dołóż nagłówek do `/admin/*`:
`-H "X-Admin-Token: TWOJ_TOKEN"`.

---

## 12. Zachowanie blokady wg polityki (`block_save`)

Domyślna polityka (`/config` → `result_policy`):

| `result` | tryb | `block_save` | override |
|---|---|---|---|
| `syntax_invalid` | hard | true | ❌ nie |
| `domain_not_found` | conditional | true | ✅ tak |
| `no_mail_capability` | conditional | true | ✅ tak |
| `typo_suspected` | warn | false | — |
| `disposable` | warn | false | — |
| `unknown` | none | **false** | — |
| `valid` | none | false | — |

**Kluczowe kryterium (§6): `unknown` NIGDY nie blokuje.** Politykę można zmienić
zmiennymi `POLICY_*` w `.env` (np. `POLICY_DISPOSABLE=hard`).

---

## 13. Testy automatyczne (dla porównania)

Ręczne przypadki mają odpowiedniki w testach:
```bash
python -m pytest -q                              # całość
python -m pytest tests/test_validation_edgecases.py -q   # edge case'y
```
