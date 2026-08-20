# Walidator adresu e-mail — PoC v2

Implementacja specyfikacji *„walidacja adresu e-mail w czasie rzeczywistym (v2, uproszczona)”*.
Jeden mikroserwis **FastAPI** (warstwy **L2–L4** + kontrakt HTTP + cache + konfigurowalne
reguły blokowania) oraz **widget JS** (**L0** składnia + **L1** literówki
ze słownikiem PL) i demo formularza CRM.

**Serwis niczego nie wysyła i nie sonduje skrzynek na żywo** — tylko *waliduje
poprawność* adresu. **Zero usług zewnętrznych, zero transferu pełnego adresu na
zewnątrz.** Do walidacji używane są wyłącznie komponenty pod naszą kontrolą:
`email-validator`, `dnspython`, lokalne listy słownikowe, cache w pamięci procesu.

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
| **L0** składnia (RFC 5321/5322) | widget JS + serwer (`email-validator`) | ✅ |
| **L1** literówki (Damerau–Levenshtein, słownik PL) | widget JS + serwer | ✅ |
| **L2** DNS A/AAAA (NXDOMAIN vs timeout) | serwer (`dnspython`) | ✅ |
| **L3** MX + fallback na A | serwer | ✅ |
| **L4** disposable + role-based | serwer (lokalne listy) | ✅ |
| **L5** sonda SMTP | `scripts/smtp_probe.py` | ✅ jako skrypt offline (poza real-time, §5) |
| Cache DNS/MX per-domena (`TTLCache`) | serwer | ✅ (Redis opcjonalny) |
| 5 stanów UI + reguły blokowania (§7, §8) | widget JS + demo | ✅ |
| Deduplikacja w CRM (MySQL/MariaDB) | serwer (`app/crm.py`, `PyMySQL`) | ✅ opcjonalne (feature-flag, domyślnie off) |
| Zgodność imię/nazwisko ↔ adres (heurystyka) | serwer (`app/name_match.py`) | ✅ zawsze, gdy podano `name` |
| Dopracowanie zgodności własnym modelem **AI** | serwer (`app/ai_client.py`, OpenAI-compatible) | ✅ opcjonalne (przełącznik, domyślnie off) |
| **Panel konfiguracji** (`/admin`) — włączniki AI/CRM | serwer + `static/admin.html` | ✅ |
| **Walidacja wsadowa CSV** + raport (`/batch`, `POST /validate/csv`) | serwer (`app/batch.py`) + `static/batch.html` | ✅ |

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

> Pola `block_override_allowed` i `exists_in_crm` to rozszerzenia względem §6 (additive).
> `block_override_allowed` mówi widgetowi, czy pokazać checkbox „potwierdzam ręcznie”
> przy blokadzie warunkowej. `exists_in_crm` (`true`/`false`/`null`) pochodzi z
> opcjonalnej deduplikacji CRM — `null`, gdy integracja jest wyłączona.

**Deduplikacja w CRM (opcjonalna, MySQL/MariaDB):**
- Serwis może sprawdzić, czy adres **już istnieje** w bazie CRM, i zwrócić to w polu
  `exists_in_crm` odpowiedzi `/validate`. To informacja **czysto pomocnicza** — nie
  wpływa na `block_save` (system nadal tylko *waliduje poprawność* adresu).
- **Feature-flag, domyślnie wyłączona.** Włącz `CRM_CHECK_ENABLED=true` i podaj dane
  połączenia (`CRM_DB_HOST`, `CRM_DB_NAME`, `CRM_DB_USER`, `CRM_DB_PASSWORD`).
  Zapytanie jest konfigurowalne (`CRM_QUERY`) — musi zawierać jeden placeholder `%s`
  (adres, lowercased); zwrócenie ≥1 wiersza oznacza „istnieje”. Parametr jest
  **bindowany** (ochrona przed SQL injection), nie sklejany w string.
- Odporność: brak sterownika `PyMySQL`, brak konfiguracji, timeout lub błąd bazy →
  `exists_in_crm = null` (nie wywraca walidacji, spójnie z zasadą §6 „unknown”).
- Stan integracji widać w `GET /config` (`crm`), bez ujawniania hasła.

**Warstwy opcjonalne + panel konfiguracji (`/admin`):**

Dwie niezależne warstwy, każdą można włączyć/wyłączyć **w locie** z panelu
`/admin` (dowolna kombinacja — albo żadna). Żadna **nie wpływa na `block_save`** —
to sygnały informacyjne (spójne z §6). Szczegóły: `docs/SPEC-panel-integracje.md`.

- **Zgodność imię/nazwisko ↔ adres.** Gdy front poda pole `name`, serwis liczy
  deterministyczną heurystykę (`app/name_match.py`, obsługuje polskie znaki) i
  zwraca `name_email_match` (`match`/`partial`/`mismatch`/`unknown`) oraz
  `name_suggestion` (poprawione imię, gdy widać literówkę — np. `Kamil Ftyka` →
  `Kamil Fityka`). **AI** (przełącznik `ai`) dopracowuje werdykt **własnym
  modelem** (OpenAI-compatible `AI_BASE_URL`/`AI_MODEL`); przy błędzie zostaje
  wynik heurystyki. `name_match_source` = `heuristic` albo `ai`.
- **CRM** (przełącznik `crm`) — deduplikacja, jak wyżej.

Panel: `GET /admin` (strona z włącznikami), `GET/POST /admin/settings` (API).
Przełączniki są **utrwalane** (`RUNTIME_SETTINGS_PATH`) i przetrwają restart.
Jeśli ustawisz `ADMIN_TOKEN`, panel i API wymagają nagłówka `X-Admin-Token`.

> **Świadomie poza zakresem serwisu real-time:** weryfikacja istnienia skrzynki
> przez SMTP (sonda `RCPT`) — grozi blacklistą IP i jest zawodna u dużych
> dostawców (§5). Dostępna wyłącznie jako **offline** skrypt z crona
> (`scripts/smtp_probe.py`), nigdy w formularzu. Serwis nie wysyła też żadnych
> e-maili (brak double opt-in) — ogranicza się do walidacji poprawności.

**Pomocnicze:** `GET /healthz`, `GET /config` (podgląd polityki + stan warstw),
`GET /admin` (panel włączników AI/CRM), `GET /batch` (wgrywanie CSV), `GET /` (demo).

## Walidacja wsadowa — wgraj CSV, dostań raport

Do jednorazowej oceny istniejącej bazy kontaktów (np. „jak wygląda nasza lista
1000 adresów?"). **Te same warstwy i ta sama polityka blokowania co w `/validate`** —
batch niczego nie luzuje ani nie zaostrza, tylko zbiera wyniki i liczy statystyki.

**Przez przeglądarkę:** `GET /batch` — przeciągasz plik, dostajesz podsumowanie,
filtry po wyniku i przycisk „Pobierz raport CSV".

**Przez API:**

```bash
curl -F "file=@kontakty.csv" "http://localhost:8000/validate/csv?format=json"
curl -F "file=@kontakty.csv" "http://localhost:8000/validate/csv?format=csv" -o raport.csv
# tylko tanie warstwy, bez DNS:
curl -F "file=@kontakty.csv" "http://localhost:8000/validate/csv?checks=syntax,typo,lists"
```

**Z linii poleceń** (bez limitu czasu HTTP — do większych baz, z crona):

```bash
python3 scripts/waliduj_csv.py kontakty.csv -o raport.csv --json raport.json
python3 scripts/generuj_przyklad_csv.py --n 1000    # przykładowa "brudna" baza
```

**Wejście:** `id, email, imie, nazwisko`. Separator (`,` `;` tab `|`), kodowanie
(UTF-8/BOM, CP1250) i aliasy nagłówków (`e-mail`, `first_name`, `surname`…)
wykrywane automatycznie; wiersze bez adresu są pomijane i policzone w raporcie.

**Wyjście — raport „co przeszło, a co nie”.** Każdy wiersz jest rozbity na te same
kroki, które demo formularza pokazuje jako „Wykonane kroki”:

| Kolumna | Kryterium |
|---|---|
| `skladnia` | poprawny format adresu (L0) |
| `literowka` | brak literówki w domenie (L1) |
| `domena_dns` | domena istnieje (L2) |
| `poczta_mx` | domena przyjmuje pocztę — rekord MX (L3) |
| `listy` | brak zastrzeżeń na listach: jednorazowe / funkcyjne (L4) |
| `imie_adres` | imię i nazwisko pasują do adresu (heurystyka, opcjonalnie AI) |
| `wynik` | kod `result` (`valid`, `typo_suspected`, …) |
| `zapis` | `dozwolony` / `zablokowany (twardo)` / `zablokowany (możliwe potwierdzenie ręczne)` |
| `uwagi` | opis tego, co nie przeszło (np. `Literowka: podejrzenie literowki -> jan@gmail.com`) |

Wartości kroków: `ok`, `ostrzezenie`, `blad`, `nieustalone` (np. timeout DNS — nigdy
nie blokuje), `pominieto` (krok się nie wykonał, bo wcześniejszy przerwał ścieżkę).
`?columns=full` (CLI: `--pelny`) dokłada surowe pola kontraktu §6 —
`domain_status`, `has_mx`, `disposable`, `role_based`, `suggestion`,
`name_email_match`, oznaczenie duplikatu i komunikat PL.

W podsumowaniu: **ile wierszy przeszło każdy z kroków**, rozkład wyników, liczba
blokad, domeny generujące najwięcej problemów, duplikaty i czas.

> **Zgodność imię ↔ adres jest sygnałem informacyjnym** — nie wpływa na `block_save`
> (§6). Można ją wyłączyć: `?name_match=false` albo `--no-name-match` w CLI.

Limity (z env): `BATCH_MAX_ROWS` (5 000), `BATCH_MAX_BYTES` (5 MB),
`BATCH_MAX_WORKERS` (8 — równoległość zapytań DNS). Powtórzony adres jest walidowany
raz, a cache DNS per-domena sprawia, że 1000 adresów to kilkadziesiąt zapytań DNS
(pomiar na przykładowej bazie: **1000 wierszy w ~0,8 s**, patrz
`docs/RAPORT-PRZYKLADOWY.md`).

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
    nameInput: document.querySelector('#fullname'), // opcjonalnie: zgodność imię↔email
    serviceUrl: 'https://walidator.crm.docker',
    saveButton: document.querySelector('#save')   // egzekwuje reguły §8
  });
</script>
```

Widget robi L0+L1 natychmiast w przeglądarce (0 ms sieci), a `/validate` woła po
**debounce ~300 ms** i na `onblur` — nie na każdy znak (§9).

## L5 — sonda SMTP (offline, opcjonalna, poza real-time)

Niezależny skrypt z crona na **liście** adresów — **nigdy** w formularzu (§5, §L5).
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
  config.py       # polityka blokowania, TTL, DNS, CRM, AI (wszystko z env)
  models.py       # modele Pydantic (kontrakt §6)
  validation.py   # L0–L4: składnia, literówki, DNS/MX, listy, orkiestracja
  batch.py        # walidacja wsadowa CSV: parsowanie, dedup, agregaty raportu
  crm.py          # opcjonalna deduplikacja w CRM (MySQL/MariaDB, feature-flag)
  name_match.py   # zgodność imię/nazwisko ↔ adres (heurystyka, polskie znaki)
  ai_client.py    # opcjonalny klient własnego modelu AI (OpenAI-compatible)
  runtime.py      # runtime-przełączniki AI/CRM (panel /admin, utrwalane)
  cache.py        # TTLCache per-domena (interfejs gotowy pod Redis)
  data/           # słowniki: popularne domeny PL, disposable, role-based
static/
  widget.js       # widget L0+L1, 5 stanów UI, egzekwowanie blokad
  demo.html       # demo formularza CRM
  admin.html      # panel konfiguracji (włączniki AI/CRM)
  batch.html      # wgrywanie CSV + raport dla całej bazy
scripts/
  refresh_disposable.sh   # cykliczny refresh listy disposable (cron)
  smtp_probe.py           # L5: offline sonda SMTP (poza real-time)
  przyklad_adresy.csv     # przykładowe wejście dla L5
  waliduj_csv.py          # walidacja wsadowa z CLI (ten sam rdzeń co /validate/csv)
  generuj_przyklad_csv.py # generator przykładowej bazy kontaktów
  przyklad_1000.csv       # przykładowa baza 1000 kontaktów (powtarzalna)
docs/
  SPEC-panel-integracje.md # spec warstw opcjonalnych + panelu
  RAPORT-PRZYKLADOWY.md    # wynik walidacji przykładowej bazy 1000 adresów
tests/
  test_validation.py      # warstwy L0–L4 + kontrakt §6
  test_smtp_probe.py      # L5: klasyfikacja, catch-all, degradacja
  test_crm.py             # CRM: feature-flag, deduplikacja, degradacja przy błędzie
  test_name_match.py      # heurystyka imię↔email (literówki, diakrytyki)
  test_ai_client.py       # AI: parsowanie werdyktu, degradacja przy błędzie
  test_runtime.py         # runtime-przełączniki + utrwalanie
  test_admin.py           # panel /admin + wpięcie warstw w /validate
  test_batch.py           # walidacja wsadowa: parsowanie CSV, dedup, raport, endpoint
Dockerfile, docker-compose.yml, .env.example, requirements.txt
```

## Testy

```bash
pytest -q          # 130 testów (DNS/AI mockowane — szybkie, offline)
```

## Uwagi wdrożeniowe / RODO (§13)

- **DNS/MX bez transferu danych osobowych** — odpytujemy tylko część domenową.
- **Logi** maskują lokalną część adresu (`LOG_FULL_EMAIL=false` domyślnie).
- **Serwis nic nie wysyła** — brak relaya SMTP, brak wychodzącej poczty. Serwis
  tylko *waliduje poprawność* adresu (L0–L4), więc nie ryzykuje reputacji IP.
- **Cache** — domyślnie w pamięci procesu; przy wielu instancjach lub potrzebie
  trwałości podmienić backend `cache.py` na Redis.
- **L5 (sonda SMTP)** — dostarczona jako skrypt offline z crona (`scripts/smtp_probe.py`)
  z etykietą „wynik niepewny” (§5), poza serwisem real-time, nigdy w formularzu.

## Kryteria sukcesu (§14) — pokrycie

- ✅ literówki PL i światowe wykrywane z sugestią (`gmial.com → gmail.com`);
- ✅ nieistniejące domeny / brak MX wykrywane; **NXDOMAIN ≠ timeout**;
- ✅ reakcja warstw sieciowych < 300 ms (pomiary 6–46 ms, kolejne z cache < 1 ms);
- ✅ żaden niejednoznaczny wynik (`unknown`) nie blokuje zapisu;
- ✅ zero wywołań do zewnętrznych usług; pełny adres nie opuszcza infrastruktury.
