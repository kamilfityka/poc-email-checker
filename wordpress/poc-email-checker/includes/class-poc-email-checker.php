<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Poc_Email_Checker {

	const DEFAULT_CHECKS = array( 'syntax', 'typo', 'dns', 'mx', 'lists' );

	private static $messages = array(
		'valid'              => 'Adres wyglada poprawnie',
		'syntax_invalid'     => 'Adres jest niepoprawny (sprawdz format)',
		'typo_suspected'     => 'Czy chodzilo o {suggestion}?',
		'domain_not_found'   => 'Domena {domain} nie istnieje',
		'no_mail_capability' => 'Domena {domain} nie obsluguje poczty',
		'disposable'         => 'To adres jednorazowy (domena tymczasowa)',
		'mailbox_not_found'  => 'Nie potwierdzilismy tej skrzynki',
		'unknown'            => 'Nie udalo sie w pelni zweryfikowac adresu',
	);

	private static $block_modes = array(
		'hard'        => array( 'block_save' => true, 'override_allowed' => false ),
		'conditional' => array( 'block_save' => true, 'override_allowed' => true ),
		'warn'        => array( 'block_save' => false, 'override_allowed' => false ),
		'none'        => array( 'block_save' => false, 'override_allowed' => false ),
	);

	private static $default_policy = array(
		'valid'              => 'none',
		'syntax_invalid'     => 'hard',
		'typo_suspected'     => 'warn',
		'domain_not_found'   => 'conditional',
		'no_mail_capability' => 'conditional',
		'disposable'         => 'warn',
		'mailbox_not_found'  => 'warn',
		'unknown'            => 'none',
	);

	private $popular_domains;
	private $disposable_domains;
	private $typo_max_distance;
	private $policy;
	private $cache_ttl;
	private $runtime_cache = array();

	public function __construct( $data_dir, $typo_max_distance = 2, array $policy_override = array(), $cache_ttl = 21600 ) {
		$this->popular_domains    = $this->load_lines( $data_dir . '/popular_domains.txt' );
		$this->disposable_domains = $this->load_lines( $data_dir . '/disposable_domains.txt' );
		$this->typo_max_distance  = (int) $typo_max_distance;
		$this->policy             = array_merge( self::$default_policy, $policy_override );
		$this->cache_ttl          = (int) $cache_ttl;
	}

	public function validate( $email, $checks = null ) {
		if ( null === $checks ) {
			$checks = self::DEFAULT_CHECKS;
		}
		$email = trim( (string) $email );

		$out = array(
			'email'         => $email,
			'result'        => 'valid',
			'syntax_valid'  => true,
			'domain_status' => 'not_checked',
			'has_mx'        => null,
			'disposable'    => false,
			'suggestion'    => null,
		);

		$parts       = $this->check_syntax( $email );
		$syntax_ok   = $parts[0];
		$local_part  = $parts[1];
		$domain      = $parts[2];
		$out['syntax_valid'] = $syntax_ok;
		if ( ! $syntax_ok ) {
			return $this->finalize( $out, 'syntax_invalid' );
		}

		if ( in_array( 'typo', $checks, true ) ) {
			$suggestion = $this->suggest_domain( $domain );
			if ( null !== $suggestion ) {
				$out['suggestion'] = $local_part . '@' . $suggestion;
				return $this->finalize( $out, 'typo_suspected' );
			}
		}

		$need_dns = in_array( 'dns', $checks, true ) || in_array( 'mx', $checks, true );
		if ( $need_dns ) {
			$dns    = $this->check_dns_mx( $domain );
			$status = $dns['domain_status'];
			$out['domain_status'] = $status;
			$out['has_mx']        = $dns['has_mx'];

			if ( 'not_found' === $status ) {
				return $this->finalize( $out, 'domain_not_found' );
			}
			if ( 'unknown' === $status ) {
				return $this->finalize( $out, 'unknown' );
			}
			if ( false === $dns['has_mx'] && false === $dns['has_a'] ) {
				return $this->finalize( $out, 'no_mail_capability' );
			}
		}

		if ( in_array( 'lists', $checks, true ) && $this->is_disposable( $domain ) ) {
			$out['disposable'] = true;
			return $this->finalize( $out, 'disposable' );
		}

		return $this->finalize( $out, 'valid' );
	}

	public function resolve_block( $result ) {
		$mode = isset( $this->policy[ $result ] ) ? $this->policy[ $result ] : 'none';
		return isset( self::$block_modes[ $mode ] ) ? self::$block_modes[ $mode ] : self::$block_modes['none'];
	}

	private function check_syntax( $email ) {
		if ( '' === $email || ! is_email( $email ) ) {
			return array( false, '', '' );
		}
		$at = strrpos( $email, '@' );
		if ( false === $at ) {
			return array( false, '', '' );
		}
		return array(
			true,
			strtolower( substr( $email, 0, $at ) ),
			strtolower( substr( $email, $at + 1 ) ),
		);
	}

	private function suggest_domain( $domain ) {
		if ( isset( $this->popular_domains[ $domain ] ) ) {
			return null;
		}
		$best      = null;
		$best_dist = $this->typo_max_distance + 1;
		foreach ( $this->popular_domains as $candidate => $_ ) {
			$dist = $this->damerau_levenshtein( $domain, (string) $candidate );
			if ( $dist < $best_dist ) {
				$best      = (string) $candidate;
				$best_dist = $dist;
			}
		}
		if ( null !== $best && $best_dist > 0 && $best_dist <= $this->typo_max_distance ) {
			return $best;
		}
		return null;
	}

	private function damerau_levenshtein( $a, $b ) {
		$la = strlen( $a );
		$lb = strlen( $b );
		$d  = array();
		for ( $i = 0; $i <= $la; $i++ ) {
			$d[ $i ][0] = $i;
		}
		for ( $j = 0; $j <= $lb; $j++ ) {
			$d[0][ $j ] = $j;
		}
		for ( $i = 1; $i <= $la; $i++ ) {
			for ( $j = 1; $j <= $lb; $j++ ) {
				$cost       = ( $a[ $i - 1 ] === $b[ $j - 1 ] ) ? 0 : 1;
				$d[ $i ][ $j ] = min(
					$d[ $i - 1 ][ $j ] + 1,
					$d[ $i ][ $j - 1 ] + 1,
					$d[ $i - 1 ][ $j - 1 ] + $cost
				);
				if ( $i > 1 && $j > 1 && $a[ $i - 1 ] === $b[ $j - 2 ] && $a[ $i - 2 ] === $b[ $j - 1 ] ) {
					$d[ $i ][ $j ] = min( $d[ $i ][ $j ], $d[ $i - 2 ][ $j - 2 ] + 1 );
				}
			}
		}
		return $d[ $la ][ $lb ];
	}

	private function check_dns_mx( $domain ) {
		if ( isset( $this->runtime_cache[ $domain ] ) ) {
			return $this->runtime_cache[ $domain ];
		}

		$transient_key = 'pec_dns_' . md5( $domain );
		$cached        = get_transient( $transient_key );
		if ( false !== $cached && is_array( $cached ) ) {
			$this->runtime_cache[ $domain ] = $cached;
			return $cached;
		}

		$result = $this->resolve_domain( $domain );
		$this->runtime_cache[ $domain ] = $result;
		if ( 'unknown' !== $result['domain_status'] && $this->cache_ttl > 0 ) {
			set_transient( $transient_key, $result, $this->cache_ttl );
		}
		return $result;
	}

	private function resolve_domain( $domain ) {
		$has_a = $this->dns_has( $domain, 'A' );
		if ( null === $has_a ) {
			return array( 'domain_status' => 'unknown', 'has_mx' => null, 'has_a' => null );
		}
		if ( false === $has_a ) {
			$has_aaaa = $this->dns_has( $domain, 'AAAA' );
			if ( null === $has_aaaa ) {
				return array( 'domain_status' => 'unknown', 'has_mx' => null, 'has_a' => null );
			}
			$has_a = $has_aaaa;
		}

		$has_mx = $this->dns_has( $domain, 'MX' );

		if ( false === $has_a && true !== $has_mx ) {
			$exists = ( true === $this->dns_has( $domain, 'NS' ) ) || ( true === $this->dns_has( $domain, 'SOA' ) );
			if ( ! $exists ) {
				return array( 'domain_status' => 'not_found', 'has_mx' => false, 'has_a' => false );
			}
		}

		return array( 'domain_status' => 'ok', 'has_mx' => $has_mx, 'has_a' => $has_a );
	}

	private function dns_has( $domain, $type ) {
		$error = false;
		set_error_handler(
			function () use ( &$error ) {
				$error = true;
				return true;
			}
		);
		try {
			$found = checkdnsrr( $domain, $type );
		} finally {
			restore_error_handler();
		}
		if ( $error && ! $found ) {
			return null;
		}
		return $found;
	}

	private function is_disposable( $domain ) {
		return isset( $this->disposable_domains[ $domain ] );
	}

	private function finalize( $out, $result ) {
		$out['result']     = $result;
		$out['message_pl'] = $this->message_for(
			$result,
			$this->domain_of( $out['email'] ),
			null !== $out['suggestion'] ? $out['suggestion'] : ''
		);
		$block                          = $this->resolve_block( $result );
		$out['block_save']              = $block['block_save'];
		$out['block_override_allowed']  = $block['override_allowed'];
		return $out;
	}

	private function message_for( $result, $domain, $suggestion ) {
		$tpl = isset( self::$messages[ $result ] ) ? self::$messages[ $result ] : self::$messages['unknown'];
		return strtr( $tpl, array( '{domain}' => $domain, '{suggestion}' => $suggestion ) );
	}

	private function domain_of( $email ) {
		$at = strrpos( $email, '@' );
		return false === $at ? '' : strtolower( substr( $email, $at + 1 ) );
	}

	private function load_lines( $path ) {
		if ( ! is_file( $path ) ) {
			return array();
		}
		$out   = array();
		$lines = file( $path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES );
		if ( ! $lines ) {
			return array();
		}
		foreach ( $lines as $line ) {
			$line = strtolower( trim( $line ) );
			if ( '' !== $line && 0 !== strpos( $line, '#' ) ) {
				$out[ $line ] = true;
			}
		}
		return $out;
	}
}
