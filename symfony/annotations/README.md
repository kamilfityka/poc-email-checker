# EmailChecked — wariant anotacyjny (Symfony 4.4 / 5.x, PHP 7.2+)

Custom constraint w **dokładnie tej samej konwencji co Wasz `Pesel` / `PeselValidator`**
(namespace `App\Validator\Constraints`, `@Annotation`, sygnatura
`validate($value, Constraint $constraint)`), który sprawdza e-mail **jak serwis PoC**
(warstwy L0–L4) i wpina się pod `@Assert` z **grupami** (`FirstStep`) tak samo jak
`@Assert\Email`.

## Pliki

| Plik | Odpowiednik u Was |
|---|---|
| `src/Validator/Constraints/EmailChecked.php` | jak `Pesel.php` |
| `src/Validator/Constraints/EmailCheckedValidator.php` | jak `PeselValidator.php` |
| `src/Service/EmailChecker.php` | silnik warstw L0–L4 (port `app/validation.py`) |
| `config/services.yaml` | rejestracja usług |

Pliki danych (`popular_domains.txt`, `disposable_domains.txt`)
skopiuj z [`../data/`](../data/) do `%kernel.project_dir%/data/`.

## Użycie na Waszej encji

Zamień `@Assert\Email` na `@AppAssert\EmailChecked`, `@Assert\Length` zostaje bez zmian.
Grupy działają identycznie (dziedziczone z `Constraint`):

```php
use Symfony\Component\Validator\Constraints as Assert;
use App\Validator\Constraints as AppAssert;

/**
 * @ORM\Column(type="string", length=255, nullable=true)
 * @AppAssert\EmailChecked(
 *      message="Niepoprawny adres email.",
 *      groups={"Default","FirstStep"}
 * )
 * @Assert\Length(
 *      max = 255,
 *      maxMessage = "Maksymalna liczba znaków to {{ limit }}",
 *      groups={"Default","FirstStep"}
 * )
 */
private $email;
```

Nic więcej w `saveStep()` nie trzeba zmieniać — `validateEntityByGroup($proposal, "FirstStep")`
uruchomi ten constraint dla grupy `FirstStep`, a naruszenia trafią do
`$validationResult->getViolations()` tak samo jak dotąd. Kod naruszenia
(`ConstraintViolation::getCode()`) to nazwa stanu (np. `domain_not_found`),
więc front może reagować per przypadek.

## Zachowanie (parytet z systemem)

Sekwencja i priorytet jak w `validate()` z `app/validation.py`:
`syntax_invalid > typo_suspected > domain_not_found > no_mail_capability >
disposable > unknown > valid`.

Naruszenie (błąd walidacji blokujący zapis) powstaje **tylko dla stanów
blokujących wg polityki serwisu** (`config.RESULT_POLICY`):

| Stan | Naruszenie domyślnie? | Komunikat |
|---|---|---|
| `syntax_invalid` | ✅ | wartość opcji `message` (`"Niepoprawny adres email."`) |
| `domain_not_found` | ✅ | „Domena `<d>` nie istnieje” |
| `no_mail_capability` | ✅ | „Domena `<d>` nie obsluguje poczty” |
| `typo_suspected` | ❌ (warn) | „Czy chodzilo o `<sugestia>`?” |
| `disposable` | ❌ (warn) | „To adres jednorazowy…” |
| `unknown` | ❌ (nigdy nie blokuje) | — |
| `valid` | ❌ | — |

Chcesz, żeby literówka/disposable też blokowały zapis (były błędem walidacji)?
Ustaw w anotacji `warnAsViolation=true`, albo zmień politykę w `services.yaml`
(`$policyOverride: { disposable: 'conditional' }`).

## Opcje constraintu

| Opcja | Domyślnie | Znaczenie |
|---|---|---|
| `message` | `"Niepoprawny adres email."` | komunikat dla błędu składni |
| `checks` | `{"syntax","typo","dns","mx","lists"}` | które warstwy uruchomić |
| `warnAsViolation` | `false` | czy ostrzeżenia (typo/disposable) też są naruszeniem |
| `groups` | — | grupy walidacji (jak w każdym constraincie) |

### Bez odpytywania DNS w formularzu

Zapytania DNS (A/AAAA/MX) idą synchronicznie przy walidacji — to sekundy przy
wolnym resolverze i ryzyko flaków. Jeśli w kroku formularza wolisz tego uniknąć:

```php
/**
 * @AppAssert\EmailChecked(checks={"syntax","typo","lists"}, groups={"FirstStep"})
 */
```

Wtedy działają tylko składnia + literówka + lista disposable, bez sieci.

## Rejestracja usług

Przy standardowym `autowire: true` + `autoconfigure: true` wystarczy związać
`$dataDir` (patrz `config/services.yaml`). Validator zostanie wykryty i otagowany
`validator.constraint_validator` automatycznie, a `EmailChecker` wstrzyknie się
przez autowiring.

## Testy

Unit test w stylu `PeselValidatorTest` (rozszerza `ConstraintValidatorTestCase`):
`tests/Validator/Constraints/EmailCheckedValidatorTest.php`. Jest **offline** i
deterministyczny — nie odpytuje DNS (używa `checks={"syntax","typo","lists"}`) i
korzysta z małych fixture'ów `tests/fixtures/` (`popular_domains.txt`,
`disposable_domains.txt`) zamiast pełnych list, więc jest szybki i stabilny w CI.

Pokrywa: null/pusty, poprawne adresy, błędy składni (naruszenie + kod
`syntax_invalid`), literówkę (domyślnie ostrzeżenie bez naruszenia; z
`warnAsViolation=true` naruszenie + sugestia + kod `typo_suspected`) oraz adres
disposable (analogicznie). Przy wpięciu do projektu skopiuj katalog `tests/`
(dostosuj namespace, jeśli inny niż `App\Tests\UnitTests\...`). Uruchomienie:

    vendor/bin/phpunit tests/Validator/Constraints/EmailCheckedValidatorTest.php

## Uwagi / ograniczenia

- **PHP 7.2+** — kod celowo bez typowanych properties (7.4) ani konstrukcji PHP 8;
  używa tylko składni dostępnej od 7.2 (działa też na 7.4 i 8.x).
- **`not_found` vs `unknown`**: `checkdnsrr` w PHP nie rozdziela NXDOMAIN od błędu
  chwilowego tak precyzyjnie jak `dnspython`. Przybliżamy: brak A/AAAA + brak MX +
  brak NS/SOA ⇒ `not_found`; twardy błąd resolvera ⇒ `unknown` (nie blokuje).
- **Składnia**: `FILTER_VALIDATE_EMAIL`. Dla pełnej zgodności z biblioteką z Pythona
  można podmienić `checkSyntax()` na `egulias/email-validator` (tę samą, której
  używa `symfony/mailer`).
- Wersje **na atrybutach PHP 8** (Symfony 6 i 7) są w katalogach `../symfony6/`
  i `../symfony7/`.
