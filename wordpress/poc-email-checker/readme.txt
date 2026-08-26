=== POC Email Checker ===
Requires at least: 5.0
Requires PHP: 7.2
Stable tag: 1.0.0
License: MIT

Walidacja adresow e-mail warstwami L0-L4 (skladnia, literowka, DNS/MX, lista
disposable) - port serwisu PoC do WordPressa.

== Opis ==

Wtyczka sprawdza adresy e-mail tak jak serwis PoC z repozytorium poc-email-checker,
warstwami L0-L4:

* L0 - skladnia (uzywa natywnego is_email())
* L1 - literowka "czy chodzilo o..." (Damerau-Levenshtein, lista popular_domains.txt)
* L2/L3 - DNS A/AAAA + MX (checkdnsrr, wynik cache'owany w transientach)
* L4 - lista disposable (adresy jednorazowe)

Priorytet wyniku:
syntax_invalid > typo_suspected > domain_not_found > no_mail_capability >
disposable > unknown > valid

Zasady zachowane z PoC:
* "poprawnosc" != "istnienie skrzynki" - nie pukamy SMTP-em do skrzynki,
* wynik "unknown" (chwilowy blad DNS) NIGDY nie blokuje,
* literowka robi short-circuit (nie odpytuje DNS).

== Gdzie sie wpina ==

Automatycznie (jesli aktywne):
* Rejestracja uzytkownika (registration_errors)
* Edycja profilu / zmiana e-mail (user_profile_update_errors)
* Komentarze (preprocess_comment)
* WooCommerce - walidacja checkoutu (woocommerce_after_checkout_validation)
* Contact Form 7 - pola typu email / email* (wpcf7_validate_email)

Blokada powstaje tylko dla stanow blokujacych wg polityki serwisu:
syntax_invalid (hard), domain_not_found i no_mail_capability (conditional).
typo_suspected i disposable sa domyslnie ostrzezeniami (nie blokuja).

== Endpoint REST API ==

Rejestrowany jest endpoint (dziala od razu po aktywacji wtyczki):

  GET|POST /wp-json/poc-email-checker/v1/validate

Parametry:
* email  (wymagany) - adres do sprawdzenia
* checks (opcjonalny, tablica) - ktore warstwy uruchomic; domyslnie wg filtra
          poc_email_checker_checks. Aby bez DNS: checks[]=syntax&checks[]=typo&checks[]=lists

Przyklady:

  curl "https://twoja-domena.pl/wp-json/poc-email-checker/v1/validate?email=jan@gmial.com"

  curl -X POST "https://twoja-domena.pl/wp-json/poc-email-checker/v1/validate" \
       -H "Content-Type: application/json" \
       -d '{"email":"info@firma.pl","checks":["syntax","typo","lists"]}'

Przykladowa odpowiedz (200):

  {
    "email": "jan@gmial.com",
    "result": "typo_suspected",
    "valid": false,
    "block": false,
    "block_save": false,
    "block_override_allowed": false,
    "message": "Czy chodzilo o jan@gmail.com?",
    "suggestion": "jan@gmail.com",
    "domain_status": "not_checked",
    "disposable": false,
    "has_mx": null
  }

Dostep: domyslnie endpoint jest publiczny (przydatny do walidacji "na zywo"
w formularzu). Mozna to zawezic filtrem:

  add_filter( 'poc_email_checker_rest_permission', function ( $allowed, $request ) {
      return is_user_logged_in(); // albo current_user_can( ... ), sprawdzenie nonce itp.
  }, 10, 2 );

Uwaga: endpoint odpytuje DNS (chyba ze ograniczysz 'checks'), wiec przy publicznym
dostepie rozwaz rate-limiting / cache po stronie serwera.

== Uzycie we wlasnym kodzie ==

  $res = poc_email_checker_validate( 'jan@gmial.com' );
  // $res['result']     => 'typo_suspected'
  // $res['suggestion'] => 'jan@gmail.com'
  // $res['message_pl'] => 'Czy chodzilo o jan@gmail.com?'

  $check = poc_email_checker_should_block( 'ktos@nieistnieje-xyz.pl' );
  // $check['block']   => true
  // $check['message'] => 'Domena nieistnieje-xyz.pl nie istnieje'

== Filtry (konfiguracja bez edycji kodu) ==

* poc_email_checker_checks           - ktore warstwy uruchamiac; domyslnie
                                       array('syntax','typo','dns','mx','lists').
                                       Aby NIE odpytywac DNS w formularzach:
                                         add_filter('poc_email_checker_checks',
                                           function(){ return array('syntax','typo','lists'); });
* poc_email_checker_block_warn       - gdy zwroci true, ostrzezenia (typo/disposable)
                                       tez blokuja wyslanie formularza.
* poc_email_checker_policy_override  - nadpisanie polityki result->tryb, np.
                                         array('disposable' => 'conditional').
* poc_email_checker_typo_max_distance- prog odleglosci edycyjnej dla sugestii (domyslnie 2).
* poc_email_checker_cache_ttl        - TTL cache DNS w sekundach (domyslnie 6h).
* poc_email_checker_data_dir         - katalog z listami (popular/disposable).
* poc_email_checker_result           - filtr na finalny wynik walidacji.

== Instalacja ==

1. Skopiuj katalog poc-email-checker do wp-content/plugins/.
2. Aktywuj "POC Email Checker" w panelu Wtyczki.
3. (Opcjonalnie) dodaj filtry z sekcji powyzej w motywie lub wtyczce mu-plugins.

== Uwagi / ograniczenia ==

* DNS odpytywany jest synchronicznie (checkdnsrr). Przy wolnym resolverze to
  dodatkowe milisekundy/sekundy na adres - wynik jest cache'owany w transientach
  na 6h per domena. Jesli formularz ma byc maksymalnie szybki, wylacz warstwe DNS
  filtrem poc_email_checker_checks.
* checkdnsrr nie rozroznia autorytatywnie NXDOMAIN od chwilowego bledu resolvera
  tak jak dnspython - przyblizamy: brak A/AAAA + brak MX + brak NS/SOA => not_found;
  twardy blad resolvera => unknown (nie blokuje).
* Lista disposable_domains.txt sie starzeje - warto ja okresowo odswiezac.
