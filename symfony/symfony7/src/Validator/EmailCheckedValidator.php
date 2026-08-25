<?php

declare(strict_types=1);

namespace App\EmailChecker\Validator;

use App\EmailChecker\Service\EmailChecker;
use Symfony\Component\Validator\Constraint;
use Symfony\Component\Validator\ConstraintValidator;
use Symfony\Component\Validator\Exception\UnexpectedTypeException;
use Symfony\Component\Validator\Exception\UnexpectedValueException;

final class EmailCheckedValidator extends ConstraintValidator
{
    public function __construct(private readonly EmailChecker $checker)
    {
    }

    public function validate(mixed $value, Constraint $constraint): void
    {
        if (!$constraint instanceof EmailChecked) {
            throw new UnexpectedTypeException($constraint, EmailChecked::class);
        }

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
