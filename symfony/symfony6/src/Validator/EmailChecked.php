<?php

declare(strict_types=1);

namespace App\EmailChecker\Validator;

use Symfony\Component\Validator\Constraint;

/**
 * Constraint sprawdzajacy adres e-mail tak jak serwis PoC (warstwy L0-L4).
 *
 * Wersja dla Symfony 6.4 (PHP 8.1+): klasyczny konstruktor z tablica $options,
 * dziala zarowno jako atrybut PHP, jak i przez konfiguracje YAML/XML.
 *
 * Uzycie (atrybut):
 *   #[EmailChecked]
 *   private string $email;
 *
 *   // tylko skladnia + literowka, bez DNS:
 *   #[EmailChecked(checks: ['syntax', 'typo'])]
 *   private string $email;
 *
 *   // potraktuj rowniez ostrzezenia (literowka/disposable) jako blad walidacji:
 *   #[EmailChecked(warnAsViolation: true)]
 *   private string $email;
 */
#[\Attribute(\Attribute::TARGET_PROPERTY | \Attribute::TARGET_METHOD | \Attribute::IS_REPEATABLE)]
class EmailChecked extends Constraint
{
    /**
     * Ktore warstwy uruchomic: syntax, typo, dns, mx, lists.
     *
     * @var list<string>
     */
    public array $checks = ['syntax', 'typo', 'dns', 'mx', 'lists'];

    /**
     * Gdy true - wyniki w trybie "warn" (typo_suspected, disposable) tez tworza
     * naruszenie. Domyslnie false: naruszenie tylko dla stanow blokujacych zapis
     * wg polityki serwisu (syntax_invalid, domain_not_found, no_mail_capability).
     */
    public bool $warnAsViolation = false;

    /**
     * @param array<string,mixed>|list<string>|null $options
     * @param list<string>|null                     $checks
     * @param string[]|null                         $groups
     */
    public function __construct(
        ?array $options = null,
        ?array $checks = null,
        ?bool $warnAsViolation = null,
        ?array $groups = null,
        mixed $payload = null,
    ) {
        parent::__construct($options ?? [], $groups, $payload);

        $this->checks = $checks ?? $this->checks;
        $this->warnAsViolation = $warnAsViolation ?? $this->warnAsViolation;
    }
}
