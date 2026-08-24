<?php

declare(strict_types=1);

namespace App\EmailChecker\Validator;

use App\EmailChecker\Service\EmailChecker;
use Symfony\Component\Validator\Constraint;
use Symfony\Component\Validator\ConstraintValidator;
use Symfony\Component\Validator\Exception\UnexpectedTypeException;
use Symfony\Component\Validator\Exception\UnexpectedValueException;

/**
 * Validator dla constraintu EmailChecked. Deleguje logike do EmailChecker
 * (port warstw L0-L4) i tworzy naruszenie z komunikatem PL, gdy wynik jest
 * blokujacy wg polityki serwisu (albo w trybie warn, gdy warnAsViolation=true).
 *
 * Wersja dla Symfony 6.4 / PHP 8.1+.
 */
class EmailCheckedValidator extends ConstraintValidator
{
    private EmailChecker $checker;

    public function __construct(EmailChecker $checker)
    {
        $this->checker = $checker;
    }

    public function validate(mixed $value, Constraint $constraint): void
    {
        if (!$constraint instanceof EmailChecked) {
            throw new UnexpectedTypeException($constraint, EmailChecked::class);
        }

        // null / pusty ciag pomijamy - od wymagalnosci jest NotBlank/NotNull.
        if (null === $value || '' === $value) {
            return;
        }

        if (!\is_string($value) && !$value instanceof \Stringable) {
            throw new UnexpectedValueException($value, 'string');
        }

        $result = $this->checker->validate((string) $value, $constraint->checks);

        if ('valid' === $result['result']) {
            return;
        }

        $block = $this->checker->resolveBlock($result['result']);
        // Naruszenie tworzymy gdy zapis jest blokowany, albo (opcjonalnie) gdy
        // wlaczono traktowanie ostrzezen jako naruszen.
        $isViolation = $block['block_save'] || $constraint->warnAsViolation;
        if (!$isViolation) {
            return;
        }

        $this->context->buildViolation($result['message_pl'])
            ->setParameter('{{ value }}', $this->formatValue($value))
            ->setParameter('{{ suggestion }}', (string) ($result['suggestion'] ?? ''))
            ->setCode($result['result'])
            ->addViolation();
    }
}
