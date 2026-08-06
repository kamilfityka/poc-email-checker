# Walidator adresu e-mail — PoC v2

Implementacja specyfikacji *„walidacja adresu e-mail w czasie rzeczywistym (v2, uproszczona)”*.
Jeden mikroserwis **FastAPI** (warstwy walidacji + kontrakt HTTP + cache + konfigurowalne
reguły blokowania + opcjonalne **double opt-in**) oraz **widget JS** (**składnia** + **literówki**
ze słownikiem PL) i demo formularza CRM.

**Zero usług zewnętrznych, zero transferu pełnego adresu na zewnątrz.** Do walidacji
używane są wyłącznie komponenty pod naszą kontrolą: `email-validator`, `dnspython`,
lokalne listy słownikowe, cache w pamięci procesu.

## Szybki start

### Lokalnie (bez Dockera)

```bash
pip install -r requirements.txt
cp .env.example .env          # opcjonalnie — działa też bez .env
uvicorn app.main:app --reload
# http://localhost:8000  -> demo formularza CRM
```

### Docker

```bash
cp .env.example .env
docker compose up --build
# http://localhost:8000
```

## Co jest zaimplementowane

| Warstwa | Gdzie | Status |
|---|---|---|
| **Składnia** (RFC 5321/5322) | widget JS + serwer (`email-validator`) | ✅ |
| **Literówki** (Damerau–Levenshtein, słownik PL) | widget JS + serwer | ✅ |
| **DNS A/AAAA** (NXDOMAIN vs timeout) | serwer (`dnspython`) | ✅ |
| **MX + fallback na A** | serwer | ✅ |
| **Disposable + role-based** | serwer (lokalne listy) | ✅ |
| **Double opt-in** (`/verify/*`) | serwer (`smtplib`, `BackgroundTasks`) | ✅ opcjonalne |
| **Sonda SMTP** | `scripts/smtp_probe.py` | ✅ jako skrypt offline (poza real-time, §5) |
| Cache DNS/MX per-domena (`TTLCache`) | serwer | ✅ (Redis opcjonalny) |
| 5 stanów UI + reguły blokowania (§7, §8) | widget JS + demo | ✅ |

## Endpointy (kontrakt §6)

`POST /validate`

```json
// request
{ "email": "jan.kowaslki@gmial.com", "checks": ["syntax","typo","dns","mx","lists"] }
// response
{ "email": "...", "result": "typo_suspected", "block_save": false,
  "syntax_valid": true, "domain_status": "not_checked", "has_mx": null,
  "disposable": false, "role_based": false, "suggestion": "jan.kowaslki@gmail.com",
  "message_pl": "Czy chodziło o gmail.com?", "cached": false, "elapsed_ms": 6,
  "block_override_allowed": false }
```

Wartości `result`: `valid`, `syntax_invalid`, `typo_suspected`, `domain_not_found`,
`no_mail_capability`, `disposable`, `mailbox_not_found`, `unknown`.

> Pole `block_override_allowed` to jedyne rozszerzenie względem §6 (additive) —
> mówi widgetowi, czy pokazać checkbox „potwierdzam ręcznie” przy blokadzie warunkowej.

**Double opt-in (ten sam serwis):**
- `POST /verify/send` — `{ "email": "..." }` → `{ "status": "sent" }` (mail HTML w tle).
  Zwraca `already_confirmed`, jeśli adres już potwierdzony; `429` przy przekroczeniu
  limitu wysyłek (`VERIFY_RATE_MAX`).
- `GET /verify/confirm?token=…` — klient klika link z maila → **strona HTML**
  „Adres potwierdzony". Dla API: `?format=json` → `{ "email": "...", "confirmed": true }`
  (kontrakt §6). Token **jednorazowy**.
- `GET /verify/status?email=…` → `{ "email": "...", "confirmed": bool, "pending": bool }`.

Tokeny w **trwałym store SQLite** (jeden plik, przetrwa restart — klient może kliknąć
link kilka godzin później); `VERIFY_STORE=memory` przełącza na cache w pamięci.
Mail jest multipart (tekst + HTML), brandowany przez `VERIFY_COMPANY_NAME` /
`VERIFY_LOGO_URL`, wysyłany przez istniejący relay Outlook/Exchange.

**Pomocnicze:** `GET /healthz`, `GET /config` (podgląd aktywnej polityki),
`GET /` (demo formularza CRM), `GET /konsola` (interaktywna konsola walidatora).

### Konsola walidatora (`/konsola`)

Samodzielny frontend (`static/console.html`) do ręcznego testowania całego
kontraktu API bez formularza CRM:

- pole adresu z walidacją na żywo (debounce 300 ms + `blur`) lub na żądanie,
- przełączniki warstw (`syntax`/`typo`/`dns`/`mx`/`lists`) → pole `checks`,
- czytelny werdykt (5 stanów §7) z przyciskiem „Popraw" przy sugestii literówki,
- siatka wszystkich pól odpowiedzi + podgląd surowego JSON,
- sekcja double opt-in: `/verify/send` + `/verify/status` dla wpisanego adresu,
- podgląd `/config` i `/healthz`.

Czysty HTML/JS, bez build-stepu i zależności zewnętrznych — serwowany przez ten
sam serwis (`window.location.origin`), więc działa bez konfiguracji CORS.

## Reguły blokowania (§8) — w konfiguracji, nie w kodzie

Sterowane zmiennymi `POLICY_*` (patrz `.env.example`). Tryby: `hard`, `conditional`
(blok + checkbox „potwierdzam ręcznie”), `warn`, `none`.

| `result` | domyślny tryb | `block_save` |
|---|---|---|
| `syntax_invalid` | hard | **true** (bez override) |
| `domain_not_found` | conditional | true (override checkboxem) |
| `no_mail_capability` | conditional | true (override checkboxem) |
| `typo_suspected` / `disposable` / `mailbox_not_found` | warn | false |
| `unknown` | none | **false — nigdy nie blokuje** |

Rozróżnienie kluczowe: **NXDOMAIN → `domain_not_found`** (autorytatywne „nie ma”),
a **timeout/SERVFAIL → `unknown`** (nie wiemy → nie blokujemy).

## Integracja z formularzem CRM

```html
<input type="email" id="email">
<div id="email-status"></div>
<button id="save">Zapisz</button>

<script src="https://walidator.crm.docker/static/widget.js"></script>
<script>
  EmailValidator.attach({
    input: document.querySelector('#email'),
    serviceUrl: 'https://walidator.crm.docker',
    saveButton: document.querySelector('#save')   // egzekwuje reguły §8
  });
</script>
```

Widget robi składnię i literówki natychmiast w przeglądarce (0 ms sieci), a `/validate` woła po
**debounce ~300 ms** i na `onblur` — nie na każdy znak (§9).

## Sonda SMTP (offline, opcjonalna, poza real-time)

Niezależny skrypt z crona na **liście** adresów — **nigdy** w formularzu (§5).
Robi `HELO/EHLO → MAIL FROM → RCPT TO` bez `DATA`, wykrywa **catch-all** (sonduje
losowy adres w domenie) i **greylisting** (4xx), a wynik dopisuje do CSV jako flagę
`skrzynka_zweryfikowana`: `tak` / `nie` / `niejednoznacznie`.

```bash
# podgląd bez łączenia (tylko MX):
python3 scripts/smtp_probe.py --in adresy.csv --dry-run -v

# realna sonda (wymaga otwartego portu 25 wyjściowo):
python3 scripts/smtp_probe.py --in adresy.csv --out wynik.csv \
    --helo poczta.firma.pl --mail-from weryfikacja@firma.pl \
    --delay 3 --max-per-domain 20
```

Świadome ryzyka (§5), wbudowane zabezpieczenia: `--delay` (grzeczność wobec serwera),
`--max-per-domain` (limit sond), a przy zablokowanym porcie 25 / błędzie połączenia
skrypt zwraca `niejednoznacznie` (nie crashuje). **Wynik zawsze traktować jako
„niepewny"** — duzi dostawcy i domeny catch-all nie ujawniają istnienia skrzynki.

## Struktura projektu

```
app/
  main.py         # FastAPI: endpointy, montaż kontraktu, CORS, statyki
  config.py       # polityka blokowania, TTL, DNS, SMTP (wszystko z env)
  models.py       # modele Pydantic (kontrakt §6)
  validation.py   # składnia, literówki, DNS/MX, listy, orkiestracja
  cache.py        # TTLCache per-domena (interfejs gotowy pod Redis)
  verify.py       # logika double opt-in + wysyłka SMTP (multipart)
  verify_store.py # trwały store tokenów (SQLite / memory)
  verify_templates.py # mail HTML + strony potwierdzenia
  data/           # słowniki: popularne domeny PL, disposable, role-based
static/
  widget.js       # widget składnia + literówki, 5 stanów UI, egzekwowanie blokad
  demo.html       # demo formularza CRM
scripts/
  refresh_disposable.sh   # cykliczny refresh listy disposable (cron)
  smtp_probe.py           # offline sonda SMTP (poza real-time)
  przyklad_adresy.csv     # przykładowe wejście dla sondy SMTP
tests/
  test_validation.py      # warstwy walidacji + kontrakt §6
  test_verify.py          # send/confirm/status, limit, trwałość, HTML
  test_smtp_probe.py      # klasyfikacja, catch-all, degradacja
Dockerfile, docker-compose.yml, .env.example, requirements.txt
```

## Testy

```bash
pytest -q          # 42 testy (DNS/SMTP mockowane — szybkie, offline)
```

## Uwagi wdrożeniowe / RODO (§13)

- **DNS/MX bez transferu danych osobowych** — odpytujemy tylko część domenową.
- **Logi** maskują lokalną część adresu (`LOG_FULL_EMAIL=false` domyślnie).
- **SMTP relay** — istniejący Outlook/Exchange jako smarthost; brak `SMTP_HOST`
  → tryb dry-run (link w logach), więc PoC działa bez konfiguracji poczty.
- **Cache** — domyślnie w pamięci procesu; przy wielu instancjach lub potrzebie
  trwałości podmienić backend `cache.py` na Redis.
- **Sonda SMTP** — dostarczona jako skrypt offline z crona (`scripts/smtp_probe.py`)
  z etykietą „wynik niepewny” (§5), poza serwisem real-time, nigdy w formularzu.

## Kryteria sukcesu (§14) — pokrycie

- ✅ literówki PL i światowe wykrywane z sugestią (`gmial.com → gmail.com`);
- ✅ nieistniejące domeny / brak MX wykrywane; **NXDOMAIN ≠ timeout**;
- ✅ reakcja warstw sieciowych < 300 ms (pomiary 6–46 ms, kolejne z cache < 1 ms);
- ✅ żaden niejednoznaczny wynik (`unknown`) nie blokuje zapisu;
- ✅ zero wywołań do zewnętrznych usług; pełny adres nie opuszcza infrastruktury.
