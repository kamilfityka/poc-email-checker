<?php
/**
 * Plugin Name:       POC Email Checker
 * Description:        Walidacja adresow e-mail warstwami L0-L4 (skladnia, literowka, DNS/MX, lista disposable) - port serwisu PoC. Wpina sie w rejestracje, profil, komentarze, WooCommerce i Contact Form 7.
 * Version:           1.0.0
 * Requires PHP:      7.2
 * Requires at least: 5.0
 * Text Domain:       poc-email-checker
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'POC_EMAIL_CHECKER_DIR', plugin_dir_path( __FILE__ ) );

require_once POC_EMAIL_CHECKER_DIR . 'includes/class-poc-email-checker.php';
require_once POC_EMAIL_CHECKER_DIR . 'includes/rest.php';

/**
 * Zwraca wspoldzielona instancje silnika (singleton).
 *
 * @return Poc_Email_Checker
 */
function poc_email_checker() {
	static $instance = null;
	if ( null === $instance ) {
		$data_dir = apply_filters( 'poc_email_checker_data_dir', POC_EMAIL_CHECKER_DIR . 'data' );
		$typo_max = (int) apply_filters( 'poc_email_checker_typo_max_distance', 2 );
		$policy   = (array) apply_filters( 'poc_email_checker_policy_override', array() );
		$ttl      = (int) apply_filters( 'poc_email_checker_cache_ttl', 6 * HOUR_IN_SECONDS );
		$instance = new Poc_Email_Checker( $data_dir, $typo_max, $policy, $ttl );
	}
	return $instance;
}

/**
 * Waliduje adres i zwraca pelny wynik (tablica pol jak w serwisie PoC).
 *
 * @param string     $email  Adres e-mail.
 * @param array|null $checks Ktore warstwy uruchomic; null = domyslne.
 * @return array
 */
function poc_email_checker_validate( $email, $checks = null ) {
	if ( null === $checks ) {
		$checks = apply_filters( 'poc_email_checker_checks', Poc_Email_Checker::DEFAULT_CHECKS );
	}
	$result = poc_email_checker()->validate( $email, $checks );
	return apply_filters( 'poc_email_checker_result', $result, $email, $checks );
}

/**
 * Decyduje, czy adres ma zostac odrzucony i z jakim komunikatem.
 * Puste wartosci ignorujemy - od wymagalnosci sa wlasne pola/reguly formularza.
 *
 * @param string $email Adres e-mail.
 * @return array{block:bool, message:string, result:string, suggestion:?string}
 */
function poc_email_checker_should_block( $email ) {
	$email = trim( (string) $email );
	if ( '' === $email ) {
		return array( 'block' => false, 'message' => '', 'result' => 'valid', 'suggestion' => null );
	}

	$res   = poc_email_checker_validate( $email );
	$block = poc_email_checker()->resolve_block( $res['result'] );

	$warn_blocks = (bool) apply_filters( 'poc_email_checker_block_warn', false, $res );
	$should      = $block['block_save'] || ( $warn_blocks && 'valid' !== $res['result'] );

	return array(
		'block'      => (bool) $should,
		'message'    => isset( $res['message_pl'] ) ? $res['message_pl'] : '',
		'result'     => $res['result'],
		'suggestion' => isset( $res['suggestion'] ) ? $res['suggestion'] : null,
	);
}

/* -------------------------------------------------------------------------
 * Integracje z formularzami WordPressa i popularnych wtyczek.
 * ---------------------------------------------------------------------- */

add_filter(
	'registration_errors',
	function ( $errors, $sanitized_user_login, $user_email ) {
		$check = poc_email_checker_should_block( $user_email );
		if ( $check['block'] ) {
			$errors->add( 'poc_email_checker', esc_html( $check['message'] ) );
		}
		return $errors;
	},
	20,
	3
);

add_action(
	'user_profile_update_errors',
	function ( $errors, $update, $user ) {
		if ( empty( $user->user_email ) ) {
			return;
		}
		$check = poc_email_checker_should_block( $user->user_email );
		if ( $check['block'] ) {
			$errors->add( 'poc_email_checker', esc_html( $check['message'] ) );
		}
	},
	20,
	3
);

add_filter(
	'preprocess_comment',
	function ( $commentdata ) {
		$email = isset( $commentdata['comment_author_email'] ) ? $commentdata['comment_author_email'] : '';
		$check = poc_email_checker_should_block( $email );
		if ( $check['block'] ) {
			wp_die(
				esc_html( $check['message'] ),
				esc_html__( 'Blad walidacji adresu e-mail', 'poc-email-checker' ),
				array( 'response' => 400, 'back_link' => true )
			);
		}
		return $commentdata;
	}
);

add_action(
	'woocommerce_after_checkout_validation',
	function ( $data, $errors ) {
		$email = isset( $data['billing_email'] ) ? $data['billing_email'] : '';
		$check = poc_email_checker_should_block( $email );
		if ( $check['block'] ) {
			$errors->add( 'poc_email_checker', esc_html( $check['message'] ) );
		}
	},
	20,
	2
);

add_action(
	'plugins_loaded',
	function () {
		if ( ! function_exists( 'wpcf7' ) && ! class_exists( 'WPCF7' ) ) {
			return;
		}
		$cf7_validator = function ( $result, $tag ) {
			$tag  = ( $tag instanceof WPCF7_FormTag ) ? $tag : new WPCF7_FormTag( $tag );
			$name = $tag->name;
			$value = isset( $_POST[ $name ] ) ? trim( (string) wp_unslash( $_POST[ $name ] ) ) : '';
			$check = poc_email_checker_should_block( $value );
			if ( $check['block'] ) {
				$result->invalidate( $tag, $check['message'] );
			}
			return $result;
		};
		add_filter( 'wpcf7_validate_email', $cf7_validator, 20, 2 );
		add_filter( 'wpcf7_validate_email*', $cf7_validator, 20, 2 );
	}
);
