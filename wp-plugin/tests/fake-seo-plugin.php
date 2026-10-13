<?php
/**
 * Test double for Yoast SEO's outputs (must-use plugin, test environments only).
 * Active only while the option `searchos_fake_seo` is "yoast": prints a description and a canonical through the
 * same filters Yoast uses (wpseo_metadesc, wpseo_canonical) and defines WPSEO_VERSION, so the connector must
 * override them through the filters and must not print a second tag.
 */
if ( 'yoast' !== get_option( 'searchos_fake_seo' ) ) {
	return;
}
define( 'WPSEO_VERSION', '99.0-test' );
remove_action( 'wp_head', 'rel_canonical' );
add_action(
	'wp_head',
	static function () {
		$d = apply_filters( 'wpseo_metadesc', 'Original Yoast description' );
		echo '<meta name="description" content="' . esc_attr( $d ) . '" />' . "\n";
		$c = apply_filters( 'wpseo_canonical', get_permalink() );
		echo '<link rel="canonical" href="' . esc_url( $c ) . '" />' . "\n";
		// Yoast keeps everything in one @graph block.
		echo '<script type="application/ld+json" class="yoast-schema-graph">{"@context":"https://schema.org","@graph":[{"@type":"WebPage","name":"Coffee grinders"},{"@type":"Organization","name":"Old org"}]}</script>' . "\n";
	},
	1
);
