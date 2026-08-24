# Symfony EmailChecked validator

Walidator adresu e-mail dla Symfony, który sprawdza adresy **dokładnie tak jak
serwis PoC** z tego repozytorium (`app/validation.py` + `app/config.py`).
Odwzorowane są wszystkie warstwy L0–L4, priorytet wyniku, komunikaty PL oraz
polityka blokowania zapisu.

Dostępny w dwóch wariantach:

| Katalog | Symfony | PHP |
|---|---|---|
| [`symfony6/`](symfony6/) | 6.4 LTS | 8.1+ |
| [`symfony7/`](symfony7/) | 7.x | 8.2+ |

Oba warianty mają identyczne zachowanie — różnią się tylko idiomatyką klasy
`Constraint` (patrz [Różnice 6 vs 7](#różnice-między-symfony-6-a-7)).

---

## Co dokładnie sprawdza (parytet z systemem)

Ta sama sekwencja warstw i ten sam priorytet wyniku co w `validate()`
w `app/validation.py`:

| Warstwa | `checks` | Wynik (`result`) | Komunikat PL |
|---|---|---|---|
| **L0** składnia (RFC) | `syntax` | `syntax_invalid` | Adres jest niepoprawny (sprawdz format) |
| **L1** literówka (did-you-mean) | `typo` | `typo_suspected` | Czy chodzilo o `<sugestia>`? |
| **L2** DNS A/AAAA | `dns` | `domain_not_found` / `unknown` | Domena `<d>` nie istnieje / … |
| **L3** MX | `mx` | `no_mail_capability` | Domena `<d>` nie obsluguje poczty |
| **L4** disposable / role-based | `lists` | `disposable` (+ flaga `role_based`) | To adres jednorazowy … |

**Priorytet** (jak w oryginale):
`syntax_invalid > typo_suspected > domain_not_found > no_mail_capability >
disposable > unknown > valid`.

Zachowane zasady kluczowe z PoC:

- **„poprawność” ≠ „istnienie skrzynki”** — potwierdzamy tylko poprawność
  (składnia, domena, MX), nie pukamy SMTP-em do skrzynki.
- **`unknown` nigdy nie blokuje** — chwilowe błędy DNS (timeout/SERVFAIL)
  nie tworzą naruszenia.
- **literówka robi short-circuit** — przy `typo_suspected` DNS nie jest sprawdzany.
- **L1** używa **Damerau-Levenshtein** z progiem `TYPO_MAX_DISTANCE = 2`.
- listy `popular_domains.txt`, `disposable_domains.txt`, `role_based.txt`
  to te same pliki co w `app/data/` (skopiowane do [`data/`](data/)).

### Polityka blokowania → naruszenie walidacji

`EmailChecker::resolveBlock()` odwzorowuje `config.RESULT_POLICY` + `BLOCK_MODES`:

| result | tryb | `block_save` | naruszenie domyślnie? |
|---|---|---|---|
| `syntax_invalid` | hard | ✅ | ✅ |
| `domain_not_found` | conditional | ✅ (override dozwolony) | ✅ |
| `no_mail_capability` | conditional | ✅ (override dozwolony) | ✅ |
| `typo_suspected` | warn | ❌ | tylko przy `warnAsViolation: true` |
| `disposable` | warn | ❌ | tylko przy `warnAsViolation: true` |
| `unknown` | none | ❌ | ❌ |
| `valid` | none | ❌ | ❌ |

Domyślnie validator tworzy naruszenie tylko dla stanów **blokujących zapis**
(`block_save = true`). `role_based` — tak jak w systemie — jest wyłącznie flagą
informacyjną i nie tworzy naruszenia.

Kod naruszenia (`ConstraintViolation::getCode()`) to wartość `result`
(np. `domain_not_found`), więc UI/kontroler może reagować per stan.

---

## Instalacja

1. Skopiuj do swojego projektu Symfony (dobierz wariant 6 lub 7):
   ```
   src/Validator/EmailChecked.php
   src/Validator/EmailCheckedValidator.php
   src/Service/EmailChecker.php
   ```
   Namespace w plikach to `App\EmailChecker\…` — dostosuj do swojego, jeśli trzeba.

2. Skopiuj pliki danych z [`data/`](data/) do `%kernel.project_dir%/data/`
   (albo wskaż własną ścieżkę parametrem `app.email_checker.data_dir`).

3. Zarejestruj usługi — patrz `config/services.yaml` w wariancie
   ([symfony6](symfony6/config/services.yaml) / [symfony7](symfony7/config/services.yaml)).
   Przy standardowym autowire + autoconfigure wystarczy związać argument `$dataDir`.

---

## Użycie

### Jako atrybut na encji / DTO

```php
use App\EmailChecker\Validator\EmailChecked;
use Symfony\Component\Validator\Constraints as Assert;

class Contact
{
    #[Assert\NotBlank]
    #[EmailChecked]
    public string $email = '';

    // tylko składnia + literówka, bez odpytywania DNS:
    #[EmailChecked(checks: ['syntax', 'typo'])]
    public string $backupEmail = '';

    // potraktuj też ostrzeżenia (literówka/disposable) jako błąd walidacji:
    #[EmailChecked(warnAsViolation: true)]
    public string $strictEmail = '';
}
```

### Ad-hoc przez ValidatorInterface

```php
use App\EmailChecker\Validator\EmailChecked;
use Symfony\Component\Validator\Validator\ValidatorInterface;

$violations = $validator->validate($email, [new EmailChecked()]);
foreach ($violations as $v) {
    // $v->getCode()    => np. 'domain_not_found'
    // $v->getMessage() => komunikat PL
}
```

### Bezpośrednio silnik (pełny wynik jak ValidateResponse)

Gdy potrzebujesz całego kontraktu (nie tylko naruszenia):

```php
use App\EmailChecker\Service\EmailChecker;

$result = $checker->validate('jan@gmial.com');
// [
//   'result' => 'typo_suspected',
//   'suggestion' => 'jan@gmail.com',
//   'message_pl' => 'Czy chodzilo o jan@gmail.com?',
//   'block_save' => false, 'block_override_allowed' => false,
//   'syntax_valid' => true, 'domain_status' => 'not_checked',
//   'has_mx' => null, 'disposable' => false, 'role_based' => false,
// ]
```

---

## Różnice między Symfony 6 a 7

Logika (silnik `EmailChecker`) jest **identyczna**. Różni się tylko klasa
`Constraint`:

| | Symfony 6.4 (`symfony6/`) | Symfony 7 (`symfony7/`) |
|---|---|---|
| PHP | 8.1+ | 8.2+ |
| Konstruktor constraintu | klasyczny z tablicą `$options` (tradycyjny styl 6.x) | `#[HasNamedArguments]` + promocja właściwości |
| Argument `array $options` | obecny (BC z YAML/XML/annotacjami) | usunięty (w Symfony 7 nie jest już wspierany) |
| `readonly` promoted properties | nie (styl zachowawczy) | tak |

Sygnatura `ConstraintValidator::validate(mixed $value, Constraint $constraint): void`
jest w obu wersjach taka sama, dlatego plik `EmailCheckedValidator.php` różni się
tylko notką wersji.

---

## Ograniczenia / uwagi

- **Rozróżnienie `not_found` vs `unknown`**: `dnspython` w PoC autorytatywnie
  odróżnia NXDOMAIN od błędu chwilowego (timeout/SERVFAIL). Funkcje DNS w PHP
  (`checkdnsrr`) tego nie rozdzielają tak precyzyjnie — silnik przybliża:
  brak A/AAAA + brak MX + brak NS/SOA ⇒ `not_found`; twardy błąd resolvera ⇒
  `unknown`. W produkcji warto rozważyć własny resolver lub sprawdzenie z retry.
- **Cache DNS**: silnik trzyma cache per-domena w obrębie jednego procesu
  (jak `_resolver` w PoC, ale bez TTL). Dla współdzielonego cache między
  żądaniami podłącz PSR-6 (`CacheItemPoolInterface`) w miejscu `dnsCache`.
- **Składnia L0**: użyto `FILTER_VALIDATE_EMAIL`. Jeśli chcesz 1:1 zachowanie
  biblioteki `email-validator` z Pythona, podmień `checkSyntax()` na
  `egulias/email-validator` (ta sama biblioteka, której używa `symfony/mailer`).
- Warstwy CRM (deduplikacja) i AI (zgodność imię↔adres) z PoC **nie są**
  częścią tego walidatora — to sygnały informacyjne, nie walidacyjne.
