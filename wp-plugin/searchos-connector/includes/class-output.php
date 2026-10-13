<?php
/**
 * What visitors and crawlers receive: the overrides, applied in the page's HTML on the server.
 *
 * Yoast SEO and Rank Math are handled through their own filters so no tag is duplicated. Without them (or for
 * things they do not control) the page head is edited as text: an existing tag is replaced, a missing one added.
 *
 * @package SearchOS_Connector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class SearchOS_Output {

	/** @var array|null|false Overrides of the current request; false = not looked up yet. */
	private static $current = false;

	private static $buffering = false;

	public static function init() {
		// Title.
		add_filter( 'pre_get_document_title', array( __CLASS__, 'filter_title' ), 99999 );
		add_filter( 'wp_title', array( __CLASS__, 'filter_title' ), 99999 );
		add_filter( 'wpseo_title', array( __CLASS__, 'filter_title' ), 99999 );
		add_filter( 'rank_math/frontend/title', array( __CLASS__, 'filter_title' ), 99999 );
		// Description.
		add_filter( 'wpseo_metadesc', array( __CLASS__, 'filter_description' ), 99999 );
		add_filter( 'rank_math/frontend/description', array( __CLASS__, 'filter_description' ), 99999 );
		// Canonical.
		add_filter( 'get_canonical_url', array( __CLASS__, 'filter_canonical' ), 99999 );
		add_filter( 'wpseo_canonical', array( __CLASS__, 'filter_canonical' ), 99999 );
		add_filter( 'rank_math/frontend/canonical', array( __CLASS__, 'filter_canonical' ), 99999 );
		// Head surgery (meta without an SEO plugin, JSON-LD) and images.
		add_action( 'wp_head', array( __CLASS__, 'start_head_buffer' ), 0 );
		add_action( 'wp_head', array( __CLASS__, 'end_head_buffer' ), 99999 );
		add_filter( 'wp_get_attachment_image_attributes', array( __CLASS__, 'filter_attachment_attributes' ), 99999, 2 );
		add_filter( 'the_content', array( __CLASS__, 'filter_content_images' ), 99999 );
		add_filter( 'post_thumbnail_html', array( __CLASS__, 'filter_content_images' ), 99999 );
		add_filter( 'widget_text_content', array( __CLASS__, 'filter_content_images' ), 99999 );
	}

	/** 'yoast', 'rankmath' or 'none'. */
	public static function seo_plugin() {
		if ( defined( 'WPSEO_VERSION' ) ) {
			return 'yoast';
		}
		if ( class_exists( 'RankMath' ) || defined( 'RANK_MATH_VERSION' ) ) {
			return 'rankmath';
		}
		return 'none';
	}

	/** Overrides for the page being rendered, or null. */
	public static function current() {
		if ( false !== self::$current ) {
			return self::$current;
		}
		self::$current = null;
		if ( is_admin() || is_feed() || is_robots() || is_embed() ) {
			return null;
		}
		$target = null;
		if ( is_singular() ) {
			$id = (int) get_queried_object_id();
			if ( $id > 0 ) {
				$target = array(
					'type'    => 'post',
					'post_id' => $id,
				);
			}
		} elseif ( is_front_page() || is_home() ) {
			$target = array(
				'type'    => 'home',
				'post_id' => 0,
			);
		}
		if ( null === $target ) {
			return null;
		}
		$data = SearchOS_Store::read( $target );
		if ( null === $data['title'] && null === $data['meta_description'] && null === $data['canonical'] && empty( $data['jsonld'] ) && empty( $data['alt'] ) ) {
			return null;
		}
		self::$current = $data;
		return $data;
	}

	/** For tests that render several requests in one process. */
	public static function reset() {
		self::$current   = false;
		self::$buffering = false;
	}

	public static function filter_title( $title ) {
		$o = self::current();
		return ( $o && $o['title'] ) ? $o['title'] : $title;
	}

	public static function filter_description( $description ) {
		$o = self::current();
		return ( $o && $o['meta_description'] ) ? $o['meta_description'] : $description;
	}

	public static function filter_canonical( $canonical ) {
		$o = self::current();
		return ( $o && $o['canonical'] ) ? $o['canonical'] : $canonical;
	}

	// ---- head -----------------------------------------------------------------------------------------------

	/** Does the head need text surgery on this request? */
	private static function needs_head_work( $o ) {
		if ( ! $o ) {
			return false;
		}
		if ( ! empty( $o['jsonld'] ) ) {
			return true;
		}
		if ( 'none' === self::seo_plugin() && ( $o['meta_description'] || $o['canonical'] ) ) {
			return true;
		}
		// Core prints a canonical only on single pages; the filters above cannot add one elsewhere.
		return (bool) ( $o['canonical'] && ! is_singular() );
	}

	public static function start_head_buffer() {
		if ( self::$buffering || ! self::needs_head_work( self::current() ) ) {
			return;
		}
		self::$buffering = true;
		ob_start();
	}

	public static function end_head_buffer() {
		if ( ! self::$buffering ) {
			return;
		}
		self::$buffering = false;
		$head = (string) ob_get_clean();
		echo self::edit_head( $head, self::current() ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- built from escaped parts below.
	}

	/**
	 * The head markup with the overrides applied. Pure function of its inputs (tested without WordPress).
	 *
	 * @param string $head Markup printed by wp_head.
	 * @param array  $o    Overrides.
	 */
	public static function edit_head( $head, $o ) {
		$use_filters = 'none' !== self::seo_plugin();
		if ( $o['meta_description'] && ! $use_filters ) {
			$tag = '<meta name="description" content="' . esc_attr( $o['meta_description'] ) . '" />' . "\n";
			$n   = 0;
			$head = preg_replace( '/<meta\b[^>]*\bname\s*=\s*["\']description["\'][^>]*>\s*/i', '', $head, -1, $n );
			$head .= $tag;
		}
		if ( $o['canonical'] && ( ! $use_filters || ! is_singular() ) ) {
			$tag  = '<link rel="canonical" href="' . esc_url( $o['canonical'] ) . '" />' . "\n";
			$head = preg_replace( '/<link\b[^>]*\brel\s*=\s*["\'][^"\']*\bcanonical\b[^"\']*["\'][^>]*>\s*/i', '', $head );
			$head .= $tag;
		}
		if ( ! empty( $o['jsonld'] ) ) {
			$head = self::edit_jsonld( $head, $o['jsonld'] );
		}
		return $head;
	}

	private static function entity_types( $node ) {
		$out = array();
		if ( ! is_array( $node ) || ! isset( $node['@type'] ) ) {
			return $out;
		}
		foreach ( (array) $node['@type'] as $t ) {
			$parts = explode( '/', (string) $t );
			$out[] = end( $parts );
		}
		return $out;
	}

	/**
	 * Remove / replace existing JSON-LD blocks as the overrides ask, then append our own blocks.
	 * A block with a @graph loses only the matching nodes (Yoast keeps everything in one graph).
	 */
	public static function edit_jsonld( $head, $items ) {
		$own_ids = array();
		$rules   = array();
		foreach ( $items as $item ) {
			$own_ids[] = $item['id'];
			if ( ! empty( $item['replace'] ) ) {
				$rules[] = $item['replace'];
			}
		}
		$pattern = '#<script\b[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>#is';
		$head    = preg_replace_callback(
			$pattern,
			static function ( $m ) use ( $own_ids, $rules ) {
				if ( preg_match( '/\bid\s*=\s*["\']([^"\']+)["\']/i', substr( $m[0], 0, strpos( $m[0], '>' ) + 1 ), $idm ) && in_array( $idm[1], $own_ids, true ) ) {
					return '';
				}
				if ( empty( $rules ) ) {
					return $m[0];
				}
				$data = json_decode( html_entity_decode( $m[1], ENT_QUOTES ), true );
				if ( ! is_array( $data ) ) {
					foreach ( $rules as $rule ) {
						if ( 'invalid' === $rule ) {
							return '';
						}
					}
					return $m[0];
				}
				$matches = static function ( $node ) use ( $rules ) {
					$types = SearchOS_Output::entity_types_public( $node );
					foreach ( $rules as $rule ) {
						if ( is_array( $rule ) && array_intersect( $types, isset( $rule['types'] ) ? $rule['types'] : array() ) ) {
							return true;
						}
					}
					return false;
				};
				if ( isset( $data['@graph'] ) && is_array( $data['@graph'] ) ) {
					$kept = array_values( array_filter( $data['@graph'], static function ( $n ) use ( $matches ) {
						return ! $matches( $n );
					} ) );
					if ( count( $kept ) === count( $data['@graph'] ) ) {
						return $m[0];
					}
					if ( empty( $kept ) ) {
						return '';
					}
					$data['@graph'] = $kept;
					return '<script type="application/ld+json">' . self::encode( $data ) . '</script>';
				}
				return $matches( $data ) ? '' : $m[0];
			},
			$head
		);
		foreach ( $items as $item ) {
			$head .= '<script type="application/ld+json" id="' . esc_attr( $item['id'] ) . '">' . self::encode( $item['value'] ) . "</script>\n";
		}
		return $head;
	}

	public static function entity_types_public( $node ) {
		return self::entity_types( $node );
	}

	/** JSON that is safe inside a script element ('<', '>' and '&' become \u escapes). */
	public static function encode( $value ) {
		return wp_json_encode( $value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP );
	}

	// ---- images ----------------------------------------------------------------------------------------------

	public static function filter_attachment_attributes( $attr, $attachment ) {
		$o = self::current();
		if ( ! $o || empty( $o['alt'] ) || ! $attachment ) {
			return $attr;
		}
		$url = wp_get_attachment_url( $attachment->ID );
		$key = SearchOS_Store::alt_key( $url );
		if ( '' !== $key && isset( $o['alt'][ $key ] ) ) {
			$attr['alt'] = $o['alt'][ $key ];
		}
		return $attr;
	}

	/** Images written into the content (or printed by a theme) as plain <img> tags. */
	public static function filter_content_images( $html ) {
		$o = self::current();
		if ( ! $o || empty( $o['alt'] ) || ! is_string( $html ) || false === stripos( $html, '<img' ) ) {
			return $html;
		}
		return self::set_alts( $html, $o['alt'] );
	}

	public static function set_alts( $html, $map ) {
		return preg_replace_callback(
			'/<img\b[^>]*>/i',
			static function ( $m ) use ( $map ) {
				$tag = $m[0];
				if ( ! preg_match( '/\bsrc\s*=\s*(?:"([^"]*)"|\'([^\']*)\')/i', $tag, $sm ) ) {
					return $tag;
				}
				$src = html_entity_decode( '' !== $sm[1] ? $sm[1] : ( isset( $sm[2] ) ? $sm[2] : '' ), ENT_QUOTES );
				$key = SearchOS_Store::alt_key( $src );
				if ( '' === $key || ! isset( $map[ $key ] ) ) {
					return $tag;
				}
				$alt = ' alt="' . esc_attr( $map[ $key ] ) . '"';
				if ( preg_match( '/\salt\s*=\s*(?:"[^"]*"|\'[^\']*\')/i', $tag ) ) {
					return preg_replace( '/\salt\s*=\s*(?:"[^"]*"|\'[^\']*\')/i', $alt, $tag, 1 );
				}
				return preg_replace( '/<img\b/i', '<img' . $alt, $tag, 1 );
			},
			$html
		);
	}
}
