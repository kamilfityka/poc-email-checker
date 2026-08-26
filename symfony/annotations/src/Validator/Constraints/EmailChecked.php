<?php

namespace App\Validator\Constraints;

use Symfony\Component\Validator\Constraint;

/**
 * @Annotation
 * @Target({"PROPERTY", "METHOD", "ANNOTATION"})
 */
class EmailChecked extends Constraint
{
    public $message = 'Niepoprawny adres email.';

    /** @var list<string> */
    public $checks = ['syntax', 'typo', 'dns', 'mx', 'lists'];

    public $warnAsViolation = false;
}
