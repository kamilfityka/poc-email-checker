<?php

namespace App\Validator\Constraints;

use Symfony\Component\Validator\Constraint;

/**
 * Custom constraint walidujacy adres e-mail tak jak serwis PoC (warstwy L0-L4).
 *
 * Styl jak w App\Validator\Constraints\Pesel - anotacja, opcje ustawiane przez
 * publiczne wlasciwosci. Obsluguje 'groups' (dziedziczone z Constraint), wiec
 * dziala z walidacja per-grupa (np. FirstStep) tak samo jak @Assert\Email.
 *
 * Uzycie (anotacja na wlasciwosci encji):
 *
 *   use App\Validator\Constraints as AppAssert;
 *
 *   /**
 *    * @AppAssert\EmailChecked(
 *    *     message="Niepoprawny adres email.",
 *    *     groups={"Default","FirstStep"}
 *    * )
 *    *\/
 *   private $email;
 *
 * @Annotation
 * @Target({"PROPERTY", "METHOD", "ANNOTATION"})
 */
class EmailChecked extends Constraint
{
    /**
     * Komunikat dla bledu skladni (stan syntax_invalid). Domyslnie jak
     * dotychczasowy @Assert\Email. Dla pozostalych stanow (literowka, brak
     * domeny, brak MX, disposable) uzywany jest komunikat PL z silnika.
     *
     * @var string
     */
    public $message = 'Niepoprawny adres email.';

    /**
     * Ktore warstwy uruchomic: syntax, typo, dns, mx, lists.
     * Aby NIE odpytywac DNS w formularzu, ustaw np. {"syntax","typo","lists"}.
     *
     * @var array
     */
    public $checks = ['syntax', 'typo', 'dns', 'mx', 'lists'];

    /**
     * Gdy true - wyniki w trybie "warn" (typo_suspected, disposable) tez tworza
     * naruszenie. Domyslnie false: naruszenie tylko dla stanow blokujacych zapis
     * wg polityki serwisu (syntax_invalid, domain_not_found, no_mail_capability).
     *
     * @var bool
     */
    public $warnAsViolation = false;
}
