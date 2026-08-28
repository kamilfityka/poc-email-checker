<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

add_action( 'rest_api_init', 'poc_email_checker_register_rest_routes' );

function poc_email_checker_register_rest_routes() {
	register_rest_route(
		'poc-email-checker/v1',
		'/validate',
		array(
			'methods'             => array( 'GET', 'POST' ),
			'callback'            => 'poc_email_checker_rest_validate',
			'permission_callback' => 'poc_email_checker_rest_permission',
			'args'                => array(
				'email'  => array(
					'required'          => true,
					'type'              => 'string',
					'sanitize_callback' => 'sanitize_text_field',
				),
				'checks' => array(
					'required' => false,
					'type'     => 'array',
					'items'    => array( 'type' => 'string' ),
				),
			),
		)
	);
}

function poc_email_checker_rest_permission( WP_REST_Request $request ) {
	return apply_filters( 'poc_email_checker_rest_permission', true, $request );
}

function poc_email_checker_rest_validate( WP_REST_Request $request ) {
	$email  = (string) $request->get_param( 'email' );
	$checks = $request->get_param( 'checks' );
	if ( ! is_array( $checks ) ) {
		$checks = null;
	}

	$result = poc_email_checker_validate( $email, $checks );

	$block = poc_email_checker()->resolve_block( $result['result'] );
	$warn  = (bool) apply_filters( 'poc_email_checker_block_warn', false, $result );

	$response = array(
		'email'                  => $result['email'],
		'result'                 => $result['result'],
		'valid'                  => ( 'valid' === $result['result'] ),
		'block'                  => $block['block_save'] || ( $warn && 'valid' !== $result['result'] ),
		'block_save'             => $block['block_save'],
		'block_override_allowed' => $block['override_allowed'],
		'message'                => $result['message_pl'],
		'suggestion'             => $result['suggestion'],
		'domain_status'          => $result['domain_status'],
		'disposable'             => $result['disposable'],
		'has_mx'                 => $result['has_mx'],
	);

	return rest_ensure_response( $response );
}
