<?php

namespace App\Validator\Constraints;

use App\Service\EmailChecker;
use Symfony\Component\Validator\Constraint;
use Symfony\Component\Validator\ConstraintValidator;
use Symfony\Component\Validator\Exception\UnexpectedTypeException;
use Symfony\Component\Validator\Exception\UnexpectedValueException;

class EmailCheckedValidator extends ConstraintValidator
{
    /** @var EmailChecker */
    private $checker;

    public function __construct(EmailChecker $checker)
    {
        $this->checker = $checker;
    }

    /**
     * @param mixed $value
     */
    public function validate($value, Constraint $constraint)
    {
        if (!$constraint instanceof EmailChecked) {
            throw new UnexpectedTypeException($constraint, EmailChecked::class);
        }

        if (null === $value || '' === $value) {
            return;
        }

        if (!is_string($value)) {
            throw new UnexpectedValueException($value, 'string');
        }

        $result = $this->checker->validate($value, $constraint->checks);

        if ('valid' === $result['result']) {
            return;
        }

        $block = $this->checker->resolveBlock($result['result']);
        if (!$block['block_save'] && !$constraint->warnAsViolation) {
            return;
        }

        $message = ('syntax_invalid' === $result['result'] && $constraint->message)
            ? $constraint->message
            : $result['message_pl'];

        $this->context->buildViolation($message)
            ->setParameter('{{ value }}', (string) $value)
            ->setParameter('{{ string }}', (string) $value)
            ->setParameter('{{ suggestion }}', (string) ($result['suggestion'] ?? ''))
            ->setCode($result['result'])
            ->addViolation();
    }
}
