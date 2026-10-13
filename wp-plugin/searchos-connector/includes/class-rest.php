<?php
/**
 * REST API: /wp-json/searchos/v1/...  Authenticated with a WordPress Application Password (any user who can edit
 * the page). Nothing here is public.
 *
 * @package SearchOS_Connector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class SearchOS_Rest {

	const NS = 'searchos/v1';

	public static function register_routes() {
		register_rest_route(
			self::NS,
			'/status',
			array(
				'methods'             => 'GET',
				'callback'            => array( __CLASS__, 'status' ),
				'permission_callback' => array( __CLASS__, 'can_use' ),
			)
		);
		register_rest_route(
			self::NS,
			'/overrides',
			array(
				array(
					'methods'             => 'GET',
					'callback'            => array( __CLASS__, 'get_overrides' ),
					'permission_callback' => array( __CLASS__, 'can_use' ),
					'args'                => array(
						'url' => array(
							'required' => true,
							'type'     => 'string',
						),
					),
				),
				array(
					'methods'             => 'PUT',
					'callback'            => array( __CLASS__, 'put_overrides' ),
					'permission_callback' => array( __CLASS__, 'can_use' ),
				),
			)
		);
	}

	public static function can_use() {
		return current_user_can( 'edit_posts' );
	}

	public static function seo_plugin() {
		return SearchOS_Output::seo_plugin();
	}

	public static function status() {
		global $wp_version;
		return array(
			'plugin_version' => SEARCHOS_CONNECTOR_VERSION,
			'wp_version'     => $wp_version,
			'php_version'    => PHP_VERSION,
			'home_url'       => home_url( '/' ),
			'seo_plugin'     => self::seo_plugin(),
			'user'           => wp_get_current_user()->user_login,
		);
	}

	private static function target_for( $url ) {
		$target = SearchOS_Store::resolve( $url );
		if ( null === $target ) {
			return new WP_Error( 'searchos_unknown_url', 'That address is not a page, post or the home page of this WordPress site.', array( 'status' => 404 ) );
		}
		if ( 'post' === $target['type'] && ! current_user_can( 'edit_post', $target['post_id'] ) ) {
			return new WP_Error( 'searchos_forbidden', 'This user may not edit that page.', array( 'status' => 403 ) );
		}
		if ( 'home' === $target['type'] && ! current_user_can( 'edit_others_pages' ) ) {
			return new WP_Error( 'searchos_forbidden', 'This user may not change the home page.', array( 'status' => 403 ) );
		}
		return $target;
	}

	public static function get_overrides( WP_REST_Request $request ) {
		$target = self::target_for( $request->get_param( 'url' ) );
		if ( is_wp_error( $target ) ) {
			return $target;
		}
		return array(
			'target'    => $target,
			'overrides' => SearchOS_Store::read( $target ),
		);
	}

	/**
	 * Body: { "url": "...", "clear": ["title"], "set": {"title": "...", "meta_description": "...", "canonical": "..."},
	 *         "jsonld": {"put": [{"id", "value", "replace"}], "remove": ["id"]},
	 *         "alt": {"put": [{"src", "value"}], "remove": ["src"]} }
	 * "clear" is applied before "set". Answers with the stored overrides before and after.
	 */
	public static function put_overrides( WP_REST_Request $request ) {
		$body   = $request->get_json_params();
		$target = self::target_for( is_array( $body ) && isset( $body['url'] ) ? $body['url'] : '' );
		if ( is_wp_error( $target ) ) {
			return $target;
		}
		$before = SearchOS_Store::read( $target );
		$after  = SearchOS_Store::apply( $before, $body );
		if ( is_wp_error( $after ) ) {
			return $after;
		}
		SearchOS_Store::write( $target, $after );
		return array(
			'target' => $target,
			'before' => $before,
			'after'  => SearchOS_Store::read( $target ),
		);
	}
}
