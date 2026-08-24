<?php

declare(strict_types=1);

namespace App\EmailChecker\Validator;

use Symfony\Component\Validator\Attribute\HasNamedArguments;
use Symfony\Component\Validator\Constraint;

/**
 * Constraint sprawdzajacy adres e-mail tak jak serwis PoC (warstwy L0-L4).
 *
 * Wersja dla Symfony 7 (PHP 8.2+): uzywa #[HasNamedArguments] i promocji
 * wlasciwosci w konstruktorze - klasyczna tablica $options zostala usunieta
 * z Symfony 7.
 *
 * Uzycie:
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
final class EmailChecked extends Constraint
{
    /**
     * Ktore warstwy uruchomic: syntax, typo, dns, mx, lists.
     *
     * @var list<string>
     */
    public array $checks;

    /**
     * Gdy true - wyniki w trybie "warn" (typo_suspected, disposable) tez tworza
     * naruszenie. Domyslnie false: naruszenie tylko dla stanow blokujacych zapis
     * wg polityki serwisu (syntax_invalid, domain_not_found, no_mail_capability).
     */
    public bool $warnAsViolation;

    /**
     * @param list<string>|null $checks
     * @param string[]|null     $groups
     */
    #[HasNamedArguments]
    public function __construct(
        ?array $checks = null,
        bool $warnAsViolation = false,
        ?array $groups = null,
        mixed $payload = null,
    ) {
        parent::__construct([], $groups, $payload);

        $this->checks = $checks ?? ['syntax', 'typo', 'dns', 'mx', 'lists'];
        $this->warnAsViolation = $warnAsViolation;
    }
}
