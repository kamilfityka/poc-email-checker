<?php

declare(strict_types=1);

namespace App\EmailChecker\Validator;

use Symfony\Component\Validator\Attribute\HasNamedArguments;
use Symfony\Component\Validator\Constraint;

#[\Attribute(\Attribute::TARGET_PROPERTY | \Attribute::TARGET_METHOD | \Attribute::IS_REPEATABLE)]
final class EmailChecked extends Constraint
{
    public array $checks;

    public bool $warnAsViolation;

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
