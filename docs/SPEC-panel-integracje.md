# Specyfikacja: warstwy opcjonalne (AI / CRM) + panel konfiguracji

Rozszerzenie walidatora e-mail o dwie **niezależne, opcjonalne** warstwy oraz
**panel administracyjny** do ich włączania/wyłączania w locie. Obie mogą być
włączone w dowolnej kombinacji **albo żadna** — rdzeń walidacji poprawności
(L0–L4) działa jak dotąd, niezależnie od nich.

> **Poza zakresem serwisu real-time (świadoma decyzja, §5):** weryfikacja
> istnienia skrzynki przez SMTP (sonda `RCPT`) oraz double opt-in (wysyłka
> maila potwierdzającego). Sondowanie z naszego IP grozi blacklistą i jest
> zawodne u dużych dostawców; wysyłka maili nie jest potrzebna. SMTP pozostaje
> wyłącznie jako **offline** skrypt z crona (`scripts/smtp_probe.py`).

## 1. Zasada nadrzędna (bez zmian, §1/§6)

„Poprawność" ≠ „istnienie skrzynki" ≠ „właściwa osoba". Nowe warstwy **tylko
dokładają sygnał informacyjny** — **nie zmieniają `block_save`**. Przy każdym
błędzie/timeoucie/wyłączeniu degradują się do „nie wiem" (`null`) i nigdy nie
wywracają walidacji.

Pewność, jaką realnie dają (ustalone wcześniej):

| Warstwa | Odpowiada na pytanie | Sufit pewności |
|---|---|---|
| CRM lookup | czy adres **już mamy w bazie**? | dedup, nie „istnienie" |
| AI / heurystyka | czy **imię/nazwisko pasuje do adresu**? | miękki sygnał |

## 2. Warstwa: zgodność imię/nazwisko ↔ adres

- **Heurystyka** (`app/name_match.py`, deterministyczna, zawsze dostępna, bez
  zależności): normalizuje polskie znaki, tnie część lokalną na tokeny, liczy
  podobieństwo (odległość edycyjna). Wynik: `match` | `partial` | `mismatch` |
  `unknown`. Gdy widać literówkę we wpisanym nazwisku — zwraca sugestię
  (np. `Kamil Ftyka` → **`Kamil Fityka`**).
- **AI (opcjonalne, `app/ai_client.py`)**: jeśli włączone, dopracowuje werdykt
  heurystyki własnym modelem (OpenAI-compatible `/chat/completions`). Przy
  dowolnym błędzie → zostaje wynik heurystyki.
- Uruchamiane tylko gdy w żądaniu podano `name` i adres jest poprawny składniowo.

## 3. Warstwa: CRM lookup (istniejąca, bez zmian logiki)

`app/crm.py` — dedup w MySQL/MariaDB. Podłączona pod runtime-toggle.

## 4. Panel konfiguracji + runtime toggles

- `app/runtime.py`: przełączniki `ai` / `crm`. Domyślne wartości z ENV
  (feature-flagi), nadpisywalne z panelu i **utrwalane** w pliku JSON
  (`RUNTIME_SETTINGS_PATH`), więc przetrwają restart. `is_enabled()` każdej
  warstwy sprawdza runtime-toggle **oraz** własną konfigurację (host/baza,
  base_url/model itd.) — sam włącznik bez konfiguracji nie „udaje", że działa.
- **Endpointy**:
  - `GET  /admin/settings` — stan przełączników + czy warstwa jest skonfigurowana.
  - `POST /admin/settings` — ustawia dowolny podzbiór `{ai,crm}` (bool).
  - `GET  /admin` — strona panelu (`static/admin.html`) z dwoma włącznikami.
- **Zabezpieczenie**: jeśli `ADMIN_TOKEN` ustawione, panel i API wymagają
  nagłówka `X-Admin-Token`. Puste = otwarte (tylko PoC/dev).

## 5. Kontrakt `/validate` (pola additive)

Dokładane pola (nie ruszają istniejących ani 5 stanów §7):

```jsonc
{
  // ... dotychczasowe pola ...
  "exists_in_crm": true,                 // CRM (już było)
  "name_email_match": "partial",         // match|partial|mismatch|unknown|null
  "name_suggestion": "Kamil Fityka",     // gdy heurystyka/AI widzi literówkę
  "name_match_source": "heuristic"       // heuristic|ai|null
}
```

`ValidateRequest` zyskuje opcjonalne pole `name`.

## 6. Zachowanie domyślne

Bez konfiguracji i bez zmian w panelu: `AI=off`, `CRM=off`. Wszystkie nowe pola
= `null`, `/validate` działa **dokładnie jak dotąd**. Heurystyka imię↔email
uruchamia się tylko, gdy front poda `name`.
