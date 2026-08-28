<?php

declare(strict_types=1);

namespace App\EmailChecker\Validator;

use Symfony\Component\Validator\Constraint;

#[\Attribute(\Attribute::TARGET_PROPERTY | \Attribute::TARGET_METHOD | \Attribute::IS_REPEATABLE)]
class EmailChecked extends Constraint
{
    /** @var list<string> */
    public array $checks = ['syntax', 'typo', 'dns', 'mx', 'lists'];

    public bool $warnAsViolation = false;

    /**
     * @param array<string, mixed>|null $options
     * @param list<string>|null         $checks
     * @param string[]|null             $groups
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
