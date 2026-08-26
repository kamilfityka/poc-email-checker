<?php

namespace App\Tests\UnitTests\Validator\Constraints;

use App\Service\EmailChecker;
use App\Validator\Constraints\EmailChecked;
use App\Validator\Constraints\EmailCheckedValidator;
use Symfony\Component\Validator\Test\ConstraintValidatorTestCase;

/**
 * @group quarantine
 */
class EmailCheckedValidatorTest extends ConstraintValidatorTestCase
{
    protected function createValidator(): EmailCheckedValidator
    {
        return new EmailCheckedValidator(new EmailChecker(__DIR__ . '/../../fixtures'));
    }

    private function offline(array $extra = []): EmailChecked
    {
        return new EmailChecked(array_merge(array('checks' => array('syntax', 'typo', 'lists')), $extra));
    }

    public function testNullIsValid(): void
    {
        $this->validator->validate(null, $this->offline());
        $this->assertNoViolation();
    }

    public function testEmptyStringIsValid(): void
    {
        $this->validator->validate('', $this->offline());
        $this->assertNoViolation();
    }

    /**
     * @param string $email
     * @dataProvider validEmails
     */
    public function testValidEmail($email): void
    {
        $this->validator->validate($email, $this->offline());
        $this->assertNoViolation();
    }

    public function validEmails(): array
    {
        return array(
            array('jan.kowalski@gmail.com'),
            array('anna@wp.pl'),
        );
    }

    /**
     * @param string $email
     * @dataProvider invalidSyntax
     */
    public function testSyntaxInvalidRaises($email): void
    {
        $this->validator->validate($email, $this->offline());
        $this->buildViolation('Niepoprawny adres email.')
            ->setParameter('{{ value }}', $email)
            ->setParameter('{{ string }}', $email)
            ->setParameter('{{ suggestion }}', '')
            ->setCode('syntax_invalid')
            ->assertRaised();
    }

    public function invalidSyntax(): array
    {
        return array(
            array('nie-mail'),
            array('jan@'),
            array('@domena.pl'),
            array('jan @gmail.com'),
        );
    }

    public function testTypoIsWarningNotBlockingByDefault(): void
    {
        $this->validator->validate('jan@gmial.com', $this->offline());
        $this->assertNoViolation();
    }

    public function testTypoRaisesWhenWarnAsViolation(): void
    {
        $this->validator->validate('jan@gmial.com', $this->offline(array('warnAsViolation' => true)));
        $this->buildViolation('Czy chodzilo o jan@gmail.com?')
            ->setParameter('{{ value }}', 'jan@gmial.com')
            ->setParameter('{{ string }}', 'jan@gmial.com')
            ->setParameter('{{ suggestion }}', 'jan@gmail.com')
            ->setCode('typo_suspected')
            ->assertRaised();
    }

    public function testDisposableIsWarningNotBlockingByDefault(): void
    {
        $this->validator->validate('ktos@mailinator.com', $this->offline());
        $this->assertNoViolation();
    }

    public function testDisposableRaisesWhenWarnAsViolation(): void
    {
        $this->validator->validate('ktos@mailinator.com', $this->offline(array('warnAsViolation' => true)));
        $this->buildViolation('To adres jednorazowy (domena tymczasowa)')
            ->setParameter('{{ value }}', 'ktos@mailinator.com')
            ->setParameter('{{ string }}', 'ktos@mailinator.com')
            ->setParameter('{{ suggestion }}', '')
            ->setCode('disposable')
            ->assertRaised();
    }
}
