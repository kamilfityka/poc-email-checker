<?php

declare(strict_types=1);

namespace App\EmailChecker\Validator;

use Symfony\Component\Validator\Constraint;

#[\Attribute(\Attribute::TARGET_PROPERTY | \Attribute::TARGET_METHOD | \Attribute::IS_REPEATABLE)]
class EmailChecked extends Constraint
{
    public array $checks = ['syntax', 'typo', 'dns', 'mx', 'lists'];

    public bool $warnAsViolation = false;

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
