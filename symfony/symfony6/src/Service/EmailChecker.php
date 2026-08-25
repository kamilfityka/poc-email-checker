<?php

declare(strict_types=1);

namespace App\EmailChecker\Service;

final class EmailChecker
{
    public const DEFAULT_CHECKS = ['syntax', 'typo', 'dns', 'mx', 'lists'];

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

    private array $popularDomains;
    private array $disposableDomains;
    private array $dnsCache = [];
    private array $policy;

    public function __construct(
        string $dataDir,
        private readonly int $typoMaxDistance = 2,
        array $policyOverride = [],
    ) {
        $this->popularDomains = $this->loadLines($dataDir.'/popular_domains.txt');
        $this->disposableDomains = $this->loadLines($dataDir.'/disposable_domains.txt');
        $this->policy = array_merge(self::DEFAULT_POLICY, $policyOverride);
    }

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
            'suggestion' => null,
        ];

        [$syntaxOk, $localPart, $domain] = $this->checkSyntax($email);
        $out['syntax_valid'] = $syntaxOk;
        if (!$syntaxOk) {
            return $this->finalize($out, 'syntax_invalid');
        }

        if (\in_array('typo', $checks, true)) {
            $suggestion = $this->suggestDomain($domain);
            if (null !== $suggestion) {
                $out['suggestion'] = $localPart.'@'.$suggestion;

                return $this->finalize($out, 'typo_suspected');
            }
        }

        $needDns = \in_array('dns', $checks, true) || \in_array('mx', $checks, true);
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

            if (false === $dns['has_mx'] && false === $dns['has_a']) {
                return $this->finalize($out, 'no_mail_capability');
            }
        }

        if (\in_array('lists', $checks, true) && $this->isDisposable($domain)) {
            $out['disposable'] = true;

            return $this->finalize($out, 'disposable');
        }

        return $this->finalize($out, 'valid');
    }

    public function resolveBlock(string $result): array
    {
        $mode = $this->policy[$result] ?? 'none';

        return self::BLOCK_MODES[$mode] ?? self::BLOCK_MODES['none'];
    }

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
        $la = \strlen($a);
        $lb = \strlen($b);
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
                    $d[$i - 1][$j] + 1,
                    $d[$i][$j - 1] + 1,
                    $d[$i - 1][$j - 1] + $cost
                );
                if ($i > 1 && $j > 1 && $a[$i - 1] === $b[$j - 2] && $a[$i - 2] === $b[$j - 1]) {
                    $d[$i][$j] = min($d[$i][$j], $d[$i - 2][$j - 2] + 1);
                }
            }
        }

        return $d[$la][$lb];
    }

    private function checkDnsMx(string $domain): array
    {
        if (isset($this->dnsCache[$domain])) {
            return $this->dnsCache[$domain];
        }
        $result = $this->resolveDomain($domain);
        if ('unknown' !== $result['domain_status']) {
            $this->dnsCache[$domain] = $result;
        }

        return $result;
    }

    private function resolveDomain(string $domain): array
    {
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

        $hasMx = $this->dnsHas($domain, 'MX');
        if (null === $hasMx) {
            $hasMx = null;
        }

        if (false === $hasA && true !== $hasMx) {
            $exists = (true === $this->dnsHas($domain, 'NS')) || (true === $this->dnsHas($domain, 'SOA'));
            if (!$exists) {
                return ['domain_status' => 'not_found', 'has_mx' => false, 'has_a' => false];
            }
        }

        return ['domain_status' => 'ok', 'has_mx' => $hasMx, 'has_a' => $hasA];
    }

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
            return null;
        }

        return $found;
    }

    private function isDisposable(string $domain): bool
    {
        return isset($this->disposableDomains[$domain]);
    }

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

    private function loadLines(string $path): array
    {
        if (!is_file($path)) {
            return [];
        }
        $out = [];
        foreach (file($path, \FILE_IGNORE_NEW_LINES | \FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
            $line = strtolower(trim($line));
            if ('' !== $line && !str_starts_with($line, '#')) {
                $out[$line] = true;
            }
        }

        return $out;
    }
}
