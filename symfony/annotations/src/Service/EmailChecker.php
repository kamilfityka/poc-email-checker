<?php

declare(strict_types=1);

namespace App\Service;

/**
 * Silnik walidacji adresu e-mail - wierny port warstw L0-L4 z serwisu PoC
 * (app/validation.py). Wariant zgodny z PHP 7.4+ (Symfony 4.4 / 5.x, anotacje).
 *
 * Kluczowa zasada (jak w oryginale): "poprawnosc" != "istnienie skrzynki".
 * Potwierdzamy tylko poprawnosc (skladnia, domena, MX). Wynikow niejednoznacznych
 * ('unknown') NIGDY nie traktujemy jako bledu blokujacego.
 *
 * Warstwy sterowane lista $checks:
 *   syntax  -> L0 skladnia (RFC)
 *   typo    -> L1 literowka (did-you-mean, Damerau-Levenshtein)
 *   dns/mx  -> L2/L3 DNS A/AAAA + MX
 *   lists   -> L4 disposable + role-based
 *
 * Priorytet wyniku (result):
 *   syntax_invalid > typo_suspected > domain_not_found > no_mail_capability
 *   > disposable > unknown > valid
 */
class EmailChecker
{
    public const DEFAULT_CHECKS = ['syntax', 'typo', 'dns', 'mx', 'lists'];

    /** Komunikaty PL (1:1 z validation.message_for w app/validation.py). */
    private const MESSAGES = [
        'valid' => 'Adres wyglada poprawnie',
        'syntax_invalid' => 'Adres jest niepoprawny (sprawdz format)',
        'typo_suspected' => 'Czy chodzilo o {suggestion}?',
        'domain_not_found' => 'Domena {domain} nie istnieje',
        'no_mail_capability' => 'Domena {domain} nie obsluguje poczty',
        'disposable' => 'To adres jednorazowy (domena tymczasowa)',
        'mailbox_not_found' => 'Nie potwierdzilismy tej skrzynki',
        'unknown' => 'Nie udalo sie w pelni zweryfikowac adresu',
    ];

    /**
     * Polityka blokowania zapisu (1:1 z config.RESULT_POLICY + BLOCK_MODES).
     * hard/conditional -> block_save=true; warn/none -> block_save=false.
     */
    private const BLOCK_MODES = [
        'hard' => ['block_save' => true, 'override_allowed' => false],
        'conditional' => ['block_save' => true, 'override_allowed' => true],
        'warn' => ['block_save' => false, 'override_allowed' => false],
        'none' => ['block_save' => false, 'override_allowed' => false],
    ];

    private const DEFAULT_POLICY = [
        'valid' => 'none',
        'syntax_invalid' => 'hard',
        'typo_suspected' => 'warn',
        'domain_not_found' => 'conditional',
        'no_mail_capability' => 'conditional',
        'disposable' => 'warn',
        'mailbox_not_found' => 'warn',
        'unknown' => 'none',
    ];

    /** @var array<string,bool> */
    private $popularDomains;
    /** @var array<string,bool> */
    private $disposableDomains;
    /** @var array<string,bool> */
    private $roleBased;
    /** @var int */
    private $typoMaxDistance;
    /** @var array<string,string> */
    private $policy;
    /** @var array<string,array> cache per-domena (w obrebie procesu) */
    private $dnsCache = [];

    /**
     * @param string               $dataDir         katalog z popular_domains.txt / disposable_domains.txt / role_based.txt
     * @param int                  $typoMaxDistance prog odleglosci edycyjnej (config.TYPO_MAX_DISTANCE)
     * @param array<string,string> $policyOverride  nadpisania polityki blokowania result->mode
     */
    public function __construct(string $dataDir, int $typoMaxDistance = 2, array $policyOverride = [])
    {
        $this->popularDomains = $this->loadLines($dataDir.'/popular_domains.txt');
        $this->disposableDomains = $this->loadLines($dataDir.'/disposable_domains.txt');
        $this->roleBased = $this->loadLines($dataDir.'/role_based.txt');
        $this->typoMaxDistance = $typoMaxDistance;
        $this->policy = array_merge(self::DEFAULT_POLICY, $policyOverride);
    }

    /**
     * Pelna walidacja jednego adresu (jak ValidateResponse w PoC, bez CRM/AI).
     *
     * @param list<string> $checks
     *
     * @return array<string,mixed>
     */
    public function validate(string $email, array $checks = self::DEFAULT_CHECKS): array
    {
        $email = trim($email);

        $out = [
            'email' => $email,
            'result' => 'valid',
            'syntax_valid' => true,
            'domain_status' => 'not_checked',
            'has_mx' => null,
            'disposable' => false,
            'role_based' => false,
            'suggestion' => null,
        ];

        // L0 - skladnia (zawsze).
        [$syntaxOk, $localPart, $domain] = $this->checkSyntax($email);
        $out['syntax_valid'] = $syntaxOk;
        if (!$syntaxOk) {
            return $this->finalize($out, 'syntax_invalid');
        }

        // L4 role-based (flaga niezalezna od result).
        if (in_array('lists', $checks, true) && '' !== $localPart) {
            $out['role_based'] = $this->isRoleBased($localPart);
        }

        // L1 - literowka. Podejrzana -> short-circuit (domain_status = not_checked).
        if (in_array('typo', $checks, true)) {
            $suggestion = $this->suggestDomain($domain);
            if (null !== $suggestion) {
                $out['suggestion'] = $localPart.'@'.$suggestion;

                return $this->finalize($out, 'typo_suspected');
            }
        }

        // L2/L3 - DNS + MX.
        $needDns = in_array('dns', $checks, true) || in_array('mx', $checks, true);
        if ($needDns) {
            $dns = $this->checkDnsMx($domain);
            $status = $dns['domain_status'];
            $out['domain_status'] = $status;
            $out['has_mx'] = $dns['has_mx'];

            if ('not_found' === $status) {
                return $this->finalize($out, 'domain_not_found');
            }
            if ('unknown' === $status) {
                return $this->finalize($out, 'unknown');
            }

            // status == ok - zdolnosc do przyjmowania poczty (L3).
            // Brak MX i brak A -> domena nie obsluguje poczty.
            // Brak MX ale jest A -> fallback wg RFC: "prawdopodobnie przyjmuje".
            if (false === $dns['has_mx'] && false === $dns['has_a']) {
                return $this->finalize($out, 'no_mail_capability');
            }
        }

        // L4 - disposable (po DNS: domena istnieje, ale jest jednorazowa).
        if (in_array('lists', $checks, true) && $this->isDisposable($domain)) {
            $out['disposable'] = true;

            return $this->finalize($out, 'disposable');
        }

        return $this->finalize($out, 'valid');
    }

    /**
     * Zwraca {block_save, override_allowed} dla danego wyniku (config.resolve_block).
     *
     * @return array{block_save:bool, override_allowed:bool}
     */
    public function resolveBlock(string $result): array
    {
        $mode = $this->policy[$result] ?? 'none';

        return self::BLOCK_MODES[$mode] ?? self::BLOCK_MODES['none'];
    }

    // --- L0: skladnia -------------------------------------------------------

    /**
     * @return array{0:bool, 1:string, 2:string} [valid, local_part(lower), domain(lower)]
     */
    private function checkSyntax(string $email): array
    {
        if ('' === $email || !filter_var($email, \FILTER_VALIDATE_EMAIL)) {
            return [false, '', ''];
        }
        $at = strrpos($email, '@');
        if (false === $at) {
            return [false, '', ''];
        }

        return [
            true,
            strtolower(substr($email, 0, $at)),
            strtolower(substr($email, $at + 1)),
        ];
    }

    // --- L1: literowki (Damerau-Levenshtein) --------------------------------

    private function suggestDomain(string $domain): ?string
    {
        if (isset($this->popularDomains[$domain])) {
            return null;
        }
        $best = null;
        $bestDist = $this->typoMaxDistance + 1;
        foreach ($this->popularDomains as $candidate => $_) {
            $dist = $this->damerauLevenshtein($domain, (string) $candidate);
            if ($dist < $bestDist) {
                $best = (string) $candidate;
                $bestDist = $dist;
            }
        }
        if (null !== $best && $bestDist > 0 && $bestDist <= $this->typoMaxDistance) {
            return $best;
        }

        return null;
    }

    private function damerauLevenshtein(string $a, string $b): int
    {
        $la = strlen($a);
        $lb = strlen($b);
        $d = [];
        for ($i = 0; $i <= $la; ++$i) {
            $d[$i][0] = $i;
        }
        for ($j = 0; $j <= $lb; ++$j) {
            $d[0][$j] = $j;
        }
        for ($i = 1; $i <= $la; ++$i) {
            for ($j = 1; $j <= $lb; ++$j) {
                $cost = ($a[$i - 1] === $b[$j - 1]) ? 0 : 1;
                $d[$i][$j] = min(
                    $d[$i - 1][$j] + 1,        // deletion
                    $d[$i][$j - 1] + 1,        // insertion
                    $d[$i - 1][$j - 1] + $cost // substitution
                );
                if ($i > 1 && $j > 1 && $a[$i - 1] === $b[$j - 2] && $a[$i - 2] === $b[$j - 1]) {
                    $d[$i][$j] = min($d[$i][$j], $d[$i - 2][$j - 2] + 1); // transposition
                }
            }
        }

        return $d[$la][$lb];
    }

    // --- L2/L3: DNS A/AAAA + MX ----------------------------------------------

    /**
     * @return array{domain_status:string, has_mx:bool|null, has_a:bool|null}
     */
    private function checkDnsMx(string $domain): array
    {
        if (isset($this->dnsCache[$domain])) {
            return $this->dnsCache[$domain];
        }
        $result = $this->resolveDomain($domain);
        if ('unknown' !== $result['domain_status']) {
            // Wynikow 'unknown' (chwilowe bledy) nie buforujemy - moga sie zmienic.
            $this->dnsCache[$domain] = $result;
        }

        return $result;
    }

    /**
     * @return array{domain_status:string, has_mx:bool|null, has_a:bool|null}
     */
    private function resolveDomain(string $domain): array
    {
        // A/AAAA (L2).
        $hasA = $this->dnsHas($domain, 'A');
        if (null === $hasA) {
            return ['domain_status' => 'unknown', 'has_mx' => null, 'has_a' => null];
        }
        if (false === $hasA) {
            $hasAaaa = $this->dnsHas($domain, 'AAAA');
            if (null === $hasAaaa) {
                return ['domain_status' => 'unknown', 'has_mx' => null, 'has_a' => null];
            }
            $hasA = $hasAaaa;
        }

        // MX (L3).
        $hasMx = $this->dnsHas($domain, 'MX');
        // null (blad resolvera na MX) -> traktujemy jak "niepewne", fallback RFC.

        // Rozroznienie not_found vs no_mail_capability. PHP (checkdnsrr) nie
        // odroznia autorytatywnie NXDOMAIN od "brak rekordu" jak dnspython -
        // przyblizamy: brak A i brak MX oraz brak NS/SOA = domena nie istnieje.
        if (false === $hasA && true !== $hasMx) {
            $exists = (true === $this->dnsHas($domain, 'NS')) || (true === $this->dnsHas($domain, 'SOA'));
            if (!$exists) {
                return ['domain_status' => 'not_found', 'has_mx' => false, 'has_a' => false];
            }
        }

        return ['domain_status' => 'ok', 'has_mx' => $hasMx, 'has_a' => $hasA];
    }

    /**
     * Sprawdza istnienie rekordu DNS danego typu.
     *
     * @return bool|null true = rekord jest, false = brak rekordu, null = blad resolvera (unknown)
     */
    private function dnsHas(string $domain, string $type): ?bool
    {
        $error = false;
        set_error_handler(static function () use (&$error): bool {
            $error = true;

            return true;
        });
        try {
            $found = checkdnsrr($domain, $type);
        } finally {
            restore_error_handler();
        }
        if ($error && !$found) {
            return null; // nie udalo sie ustalic -> unknown
        }

        return $found;
    }

    // --- L4: disposable / role-based ----------------------------------------

    private function isDisposable(string $domain): bool
    {
        return isset($this->disposableDomains[$domain]);
    }

    private function isRoleBased(string $localPart): bool
    {
        return isset($this->roleBased[$localPart]);
    }

    // --- helpery ------------------------------------------------------------

    /**
     * @param array<string,mixed> $out
     *
     * @return array<string,mixed>
     */
    private function finalize(array $out, string $result): array
    {
        $out['result'] = $result;
        $out['message_pl'] = $this->messageFor(
            $result,
            self::domainOf((string) $out['email']),
            (string) ($out['suggestion'] ?? '')
        );
        $block = $this->resolveBlock($result);
        $out['block_save'] = $block['block_save'];
        $out['block_override_allowed'] = $block['override_allowed'];

        return $out;
    }

    private function messageFor(string $result, string $domain, string $suggestion): string
    {
        $tpl = self::MESSAGES[$result] ?? self::MESSAGES['unknown'];

        return strtr($tpl, ['{domain}' => $domain, '{suggestion}' => $suggestion]);
    }

    private static function domainOf(string $email): string
    {
        $at = strrpos($email, '@');

        return false === $at ? '' : strtolower(substr($email, $at + 1));
    }

    /**
     * @return array<string,bool>
     */
    private function loadLines(string $path): array
    {
        if (!is_file($path)) {
            return [];
        }
        $out = [];
        $lines = file($path, \FILE_IGNORE_NEW_LINES | \FILE_SKIP_EMPTY_LINES);
        foreach ($lines ?: [] as $line) {
            $line = strtolower(trim($line));
            if ('' !== $line && 0 !== strpos($line, '#')) {
                $out[$line] = true;
            }
        }

        return $out;
    }
}
