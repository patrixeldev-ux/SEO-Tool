<?php
/**
 * Where the overrides live and how they are cleaned.
 *
 * Per page: post meta `_searchos_overrides` (JSON string) on the post the URL resolves to. The home page, when it
 * is not a static page, uses the option `searchos_home_overrides`. Nothing else is ever written, so removing an
 * override gives back exactly what the theme / SEO plugin produced.
 *
 * @package SearchOS_Connector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class SearchOS_Store {

	const META_KEY    = '_searchos_overrides';
	const HOME_OPTION = 'searchos_home_overrides';
	const FIELDS      = array( 'title', 'meta_description', 'canonical' );

	/**
	 * Resolve a public URL of this site to a storage target.
	 *
	 * @param string $url Absolute URL.
	 * @return array|null array( 'type' => 'post'|'home', 'post_id' => int ) or null when it is not a page of this site.
	 */
	public static function resolve( $url ) {
		$url  = esc_url_raw( (string) $url );
		$home = wp_parse_url( home_url( '/' ) );
		$u    = wp_parse_url( $url );
		if ( empty( $u['host'] ) || empty( $home['host'] ) ) {
			return null;
		}
		$strip = static function ( $h ) {
			return preg_replace( '/^www\./i', '', strtolower( $h ) );
		};
		if ( $strip( $u['host'] ) !== $strip( $home['host'] ) ) {
			return null;
		}
		$path      = isset( $u['path'] ) ? $u['path'] : '/';
		$home_path = isset( $home['path'] ) ? $home['path'] : '/';
		if ( untrailingslashit( $path ) === untrailingslashit( $home_path ) ) {
			$front = (int) get_option( 'page_on_front' );
			if ( 'page' === get_option( 'show_on_front' ) && $front > 0 ) {
				return array(
					'type'    => 'post',
					'post_id' => $front,
				);
			}
			return array(
				'type'    => 'home',
				'post_id' => 0,
			);
		}
		$post_id = (int) url_to_postid( $url );
		if ( $post_id > 0 ) {
			return array(
				'type'    => 'post',
				'post_id' => $post_id,
			);
		}
		return null;
	}

	public static function empty_overrides() {
		return array(
			'title'            => null,
			'meta_description' => null,
			'canonical'        => null,
			'jsonld'           => array(),
			'alt'              => array(),
		);
	}

	public static function read( $target ) {
		if ( 'home' === $target['type'] ) {
			$raw = get_option( self::HOME_OPTION, '' );
		} else {
			$raw = get_post_meta( $target['post_id'], self::META_KEY, true );
		}
		$data = is_string( $raw ) && '' !== $raw ? json_decode( $raw, true ) : null;
		if ( ! is_array( $data ) ) {
			$data = array();
		}
		$out = array_merge( self::empty_overrides(), $data );
		if ( ! is_array( $out['jsonld'] ) ) {
			$out['jsonld'] = array();
		}
		if ( ! is_array( $out['alt'] ) ) {
			$out['alt'] = array();
		}
		return $out;
	}

	public static function write( $target, $data ) {
		$empty = null === $data['title'] && null === $data['meta_description'] && null === $data['canonical']
			&& empty( $data['jsonld'] ) && empty( $data['alt'] );
		if ( 'home' === $target['type'] ) {
			if ( $empty ) {
				delete_option( self::HOME_OPTION );
			} else {
				update_option( self::HOME_OPTION, wp_json_encode( $data ), false );
			}
			return;
		}
		if ( $empty ) {
			delete_post_meta( $target['post_id'], self::META_KEY );
		} else {
			// wp_slash: update_post_meta unslashes, and the JSON contains backslashes.
			update_post_meta( $target['post_id'], self::META_KEY, wp_slash( wp_json_encode( $data ) ) );
		}
	}

	public static function clean_text( $value, $max = 600 ) {
		if ( ! is_string( $value ) ) {
			return null;
		}
		$value = trim( sanitize_text_field( $value ) );
		if ( '' === $value || mb_strlen( $value ) > $max ) {
			return null;
		}
		return $value;
	}

	public static function clean_url( $value ) {
		if ( ! is_string( $value ) ) {
			return null;
		}
		$url = esc_url_raw( trim( $value ), array( 'http', 'https' ) );
		return '' === $url ? null : $url;
	}

	/**
	 * A JSON-LD entity as an array, or null when it is not one JSON object with an @type.
	 */
	public static function clean_jsonld( $value ) {
		if ( is_string( $value ) ) {
			$value = json_decode( $value, true );
		}
		if ( ! is_array( $value ) || ! isset( $value['@type'] ) ) {
			return null;
		}
		$encoded = wp_json_encode( $value );
		if ( false === $encoded || strlen( $encoded ) > 20000 ) {
			return null;
		}
		return $value;
	}

	/**
	 * Apply one change request to the stored overrides.
	 *
	 * Order: clear, then set - so a rollback can clear a field and put an older value back in one call.
	 *
	 * @param array $current Stored overrides.
	 * @param array $req     Request body (see SearchOS_Rest::put_overrides).
	 * @return array|WP_Error The new overrides.
	 */
	public static function apply( $current, $req ) {
		$new = $current;
		if ( ! empty( $req['clear'] ) && is_array( $req['clear'] ) ) {
			foreach ( $req['clear'] as $field ) {
				if ( in_array( $field, self::FIELDS, true ) ) {
					$new[ $field ] = null;
				}
			}
		}
		if ( ! empty( $req['set'] ) && is_array( $req['set'] ) ) {
			foreach ( $req['set'] as $field => $value ) {
				if ( ! in_array( $field, self::FIELDS, true ) ) {
					return new WP_Error( 'searchos_bad_field', 'Unknown field: ' . sanitize_key( $field ), array( 'status' => 400 ) );
				}
				$clean = 'canonical' === $field ? self::clean_url( $value ) : self::clean_text( $value );
				if ( null === $clean ) {
					return new WP_Error( 'searchos_bad_value', 'The value for ' . $field . ' is empty, too long or not valid.', array( 'status' => 400 ) );
				}
				$new[ $field ] = $clean;
			}
		}
		$ld = isset( $req['jsonld'] ) && is_array( $req['jsonld'] ) ? $req['jsonld'] : array();
		if ( ! empty( $ld['remove'] ) && is_array( $ld['remove'] ) ) {
			$remove = array_map( 'sanitize_key', $ld['remove'] );
			$new['jsonld'] = array_values(
				array_filter(
					$new['jsonld'],
					static function ( $item ) use ( $remove ) {
						return ! in_array( $item['id'], $remove, true );
					}
				)
			);
		}
		if ( ! empty( $ld['put'] ) && is_array( $ld['put'] ) ) {
			foreach ( $ld['put'] as $item ) {
				$id     = isset( $item['id'] ) ? sanitize_key( $item['id'] ) : '';
				$entity = isset( $item['value'] ) ? self::clean_jsonld( $item['value'] ) : null;
				if ( '' === $id || null === $entity ) {
					return new WP_Error( 'searchos_bad_jsonld', 'Structured data needs an id and one JSON object with an @type.', array( 'status' => 400 ) );
				}
				$replace = isset( $item['replace'] ) ? $item['replace'] : null;
				if ( is_array( $replace ) ) {
					$replace = array( 'types' => array_values( array_map( 'sanitize_text_field', isset( $replace['types'] ) ? (array) $replace['types'] : array() ) ) );
				} elseif ( 'invalid' !== $replace ) {
					$replace = null;
				}
				$new['jsonld'] = array_values(
					array_filter(
						$new['jsonld'],
						static function ( $i ) use ( $id ) {
							return $i['id'] !== $id;
						}
					)
				);
				$new['jsonld'][] = array(
					'id'      => $id,
					'value'   => $entity,
					'replace' => $replace,
				);
			}
		}
		$alt = isset( $req['alt'] ) && is_array( $req['alt'] ) ? $req['alt'] : array();
		if ( ! empty( $alt['remove'] ) && is_array( $alt['remove'] ) ) {
			foreach ( $alt['remove'] as $src ) {
				$key = self::alt_key( $src );
				unset( $new['alt'][ $key ] );
			}
		}
		if ( ! empty( $alt['put'] ) && is_array( $alt['put'] ) ) {
			foreach ( $alt['put'] as $item ) {
				$key   = isset( $item['src'] ) ? self::alt_key( $item['src'] ) : '';
				$value = isset( $item['value'] ) ? self::clean_text( $item['value'], 300 ) : null;
				if ( '' === $key || null === $value ) {
					return new WP_Error( 'searchos_bad_alt', 'Image text needs an image address and a description.', array( 'status' => 400 ) );
				}
				$new['alt'][ $key ] = $value;
			}
		}
		return $new;
	}

	/**
	 * Images are matched by path, without scheme / host and without a "-300x200" size suffix.
	 */
	public static function alt_key( $src ) {
		if ( ! is_string( $src ) ) {
			return '';
		}
		$path = wp_parse_url( $src, PHP_URL_PATH );
		if ( ! is_string( $path ) || '' === $path ) {
			return '';
		}
		return preg_replace( '/-\d+x\d+(\.[a-z0-9]+)$/i', '$1', $path );
	}
}
