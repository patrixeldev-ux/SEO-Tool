<?php
/**
 * Runs when the plugin is deleted in WordPress: removes everything it stored.
 *
 * @package SearchOS_Connector
 */

if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
	exit;
}

delete_post_meta_by_key( '_searchos_overrides' );
delete_option( 'searchos_home_overrides' );
