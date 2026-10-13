<?php
/**
 * Plugin Name:       SearchOS Connector
 * Description:       Lets your Search OS account apply approved SEO changes (title, meta description, canonical address, structured data, image descriptions) to your pages, in the page's HTML. Works with Yoast SEO and Rank Math, or on its own.
 * Version:           0.1.0
 * Requires at least: 6.0
 * Requires PHP:      7.4
 * Author:            Patrixel
 * License:           Proprietary
 * Text Domain:       searchos-connector
 *
 * @package SearchOS_Connector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'SEARCHOS_CONNECTOR_VERSION', '0.1.0' );
define( 'SEARCHOS_CONNECTOR_DIR', plugin_dir_path( __FILE__ ) );

require_once SEARCHOS_CONNECTOR_DIR . 'includes/class-store.php';
require_once SEARCHOS_CONNECTOR_DIR . 'includes/class-rest.php';
require_once SEARCHOS_CONNECTOR_DIR . 'includes/class-output.php';

add_action( 'rest_api_init', array( 'SearchOS_Rest', 'register_routes' ) );
SearchOS_Output::init();
