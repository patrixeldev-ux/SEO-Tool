<?php
/**
 * Installs WordPress (SQLite) for tests/local-wp.sh and creates the test content + an application password.
 * Usage: php seed-local.php <wordpress dir> <host:port> <image file>
 */
list( , $root, $hostport, $image ) = $argv;
$_SERVER['HTTP_HOST'] = $hostport;
define( 'WP_INSTALLING', true );
require $root . '/wp-load.php';
require_once ABSPATH . 'wp-admin/includes/upgrade.php';
require_once ABSPATH . 'wp-admin/includes/plugin.php';
require_once ABSPATH . 'wp-admin/includes/image.php';
wp_install( 'Test WP', 'admin', 'admin@example.com', true, '', 'adminpass123' );
activate_plugin( 'searchos-connector/searchos-connector.php' );
update_option( 'permalink_structure', '/%postname%/' );
delete_option( 'rewrite_rules' ); // regenerated on the next request
$dir = wp_upload_dir();
wp_mkdir_p( $dir['path'] );
copy( $image, $dir['path'] . '/grinder.png' );
$att = wp_insert_attachment( array( 'post_mime_type' => 'image/png', 'post_title' => 'grinder', 'post_status' => 'inherit' ), $dir['path'] . '/grinder.png' );
$img = untrailingslashit( 'http://' . $hostport ) . '/wp-content/uploads' . $dir['subdir'] . '/grinder.png';
$post = wp_insert_post( array(
	'post_title'   => 'Coffee grinders',
	'post_name'    => 'coffee-grinders',
	'post_status'  => 'publish',
	'post_content' => "<p>We sell burr grinders for home brewing.</p>\n<img src=\"$img\" alt=\"\">",
) );
$pw = WP_Application_Passwords::create_new_application_password( 1, array( 'name' => 'searchos' ) );
echo json_encode( array( 'password' => $pw[0], 'post' => $post, 'img' => $img ) ), "\n";
