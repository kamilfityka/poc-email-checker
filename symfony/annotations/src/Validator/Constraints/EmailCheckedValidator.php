<?php

namespace App\Validator\Constraints;

use App\Service\EmailChecker;
use Symfony\Component\Validator\Constraint;
use Symfony\Component\Validator\ConstraintValidator;
use Symfony\Component\Validator\Exception\UnexpectedTypeException;
use Symfony\Component\Validator\Exception\UnexpectedValueException;

/**
 * Validator dla constraintu EmailChecked. Styl jak App\Validator\Constraints\
 * PeselValidator - deleguje logike do EmailChecker (port warstw L0-L4) i tworzy
 * naruszenie z komunikatem PL, gdy wynik jest blokujacy wg polityki serwisu
 * (albo w trybie warn, gdy warnAsViolation=true).
 */
class EmailCheckedValidator extends ConstraintValidator
{
    /** @var EmailChecker */
    private $checker;

    public function __construct(EmailChecker $checker)
    {
        $this->checker = $checker;
    }

    public function validate($value, Constraint $constraint)
    {
        if (!$constraint instanceof EmailChecked) {
            throw new UnexpectedTypeException($constraint, EmailChecked::class);
        }

        // Custom constraints should ignore null and empty values to allow
        // other constraints (NotBlank, NotNull, etc.) take care of that.
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
        // Naruszenie tworzymy gdy zapis jest blokowany, albo (opcjonalnie) gdy
        // wlaczono traktowanie ostrzezen jako naruszen.
        if (!$block['block_save'] && !$constraint->warnAsViolation) {
            return;
        }

        // Dla bledu skladni uzywamy komunikatu z anotacji (spojnosc z dotychczasowym
        // @Assert\Email); dla pozostalych stanow - bogatszy komunikat PL z silnika.
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
