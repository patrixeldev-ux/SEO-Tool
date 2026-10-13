#!/usr/bin/env bash
# A real WordPress for local integration tests WITHOUT Docker: PHP's built-in web server + the SQLite database
# plugin. (CI uses the official WordPress and MariaDB images instead - see .github/workflows/ci.yml, job `wordpress`.)
#
#   wp-plugin/tests/local-wp.sh start      # installs into /tmp/searchos-wp on first run, starts on :8081
#   wp-plugin/tests/local-wp.sh env        # prints the WP_TEST_* variables for the backend test
#   wp-plugin/tests/local-wp.sh seo yoast  # (or: seo none) switch the Yoast test double on / off
#   wp-plugin/tests/local-wp.sh stop
#
# Needs PHP >= 8.1 with pdo_sqlite and, once, the npm package that carries WordPress and the SQLite plugin
# (fetched with npm; wordpress.org itself is not needed).
set -euo pipefail
ROOT=/tmp/searchos-wp
PORT=8081
HOST=wp.test
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd "$HERE/.." && pwd)/searchos-connector"

install_wp() {
  [ -f "$ROOT/wp-load.php" ] && return
  mkdir -p "$ROOT-dl" && (cd "$ROOT-dl" && npm init -y >/dev/null && npm i @wp-playground/wordpress-builds >/dev/null)
  local pkg="$ROOT-dl/node_modules/@wp-playground/wordpress-builds/src"
  mkdir -p "$ROOT" && (cd "$ROOT" && unzip -q "$pkg/wordpress/wp-6.5.zip")
  mkdir -p "$ROOT-dl/sqlite" && (cd "$ROOT-dl/sqlite" && unzip -q -o "$pkg/sqlite-database-integration/sqlite-database-integration.zip")
  cp -r "$ROOT-dl"/sqlite/sqlite-database-integration-main "$ROOT/wp-content/plugins/sqlite-database-integration"
  sed -e "s#{SQLITE_IMPLEMENTATION_FOLDER_PATH}#$ROOT/wp-content/plugins/sqlite-database-integration#g" \
      -e "s#{SQLITE_PLUGIN}#sqlite-database-integration/load.php#g" \
      "$ROOT/wp-content/plugins/sqlite-database-integration/db.copy" > "$ROOT/wp-content/db.php"
  rm -f "$ROOT/wp-content/database/.ht.sqlite"
  cat > "$ROOT/wp-config.php" <<PHP
<?php
define( 'DB_NAME', 'wordpress' ); define( 'DB_USER', '' ); define( 'DB_PASSWORD', '' ); define( 'DB_HOST', '' );
define( 'DB_CHARSET', 'utf8' ); define( 'DB_COLLATE', '' );
define( 'AUTH_KEY', 'a' ); define( 'SECURE_AUTH_KEY', 'b' ); define( 'LOGGED_IN_KEY', 'c' ); define( 'NONCE_KEY', 'd' );
define( 'AUTH_SALT', 'e' ); define( 'SECURE_AUTH_SALT', 'f' ); define( 'LOGGED_IN_SALT', 'g' ); define( 'NONCE_SALT', 'h' );
\$table_prefix = 'wp_';
define( 'WP_DEBUG', true ); define( 'WP_DEBUG_DISPLAY', false ); define( 'WP_DEBUG_LOG', '$ROOT/debug.log' );
define( 'WP_ENVIRONMENT_TYPE', 'local' );
define( 'WP_HOME', 'http://$HOST:$PORT' ); define( 'WP_SITEURL', 'http://$HOST:$PORT' );
if ( ! defined( 'ABSPATH' ) ) { define( 'ABSPATH', __DIR__ . '/' ); }
require_once ABSPATH . 'wp-settings.php';
PHP
  cat > "$ROOT/router.php" <<'PHP'
<?php
$path = parse_url( $_SERVER['REQUEST_URI'], PHP_URL_PATH );
if ( $path !== '/' && file_exists( __DIR__ . $path ) && ! is_dir( __DIR__ . $path ) ) { return false; }
$_SERVER['SCRIPT_NAME'] = '/index.php';
require __DIR__ . '/index.php';
PHP
  ln -sfn "$PLUGIN" "$ROOT/wp-content/plugins/searchos-connector"
  mkdir -p "$ROOT/wp-content/mu-plugins" && cp "$HERE/fake-seo-plugin.php" "$ROOT/wp-content/mu-plugins/"
  grep -q "$HOST" /etc/hosts || echo "127.0.0.1 $HOST" >> /etc/hosts
  php "$HERE/seed-local.php" "$ROOT" "$HOST:$PORT" "$HERE/pixel.png" > "$ROOT/seed.json"
}

case "${1:-start}" in
  start)
    install_wp
    pgrep -f "php -S 0.0.0.0:$PORT" >/dev/null || (nohup php -S 0.0.0.0:$PORT -t "$ROOT" "$ROOT/router.php" > "$ROOT/server.log" 2>&1 &)
    sleep 2; curl -fsS "http://$HOST:$PORT/" >/dev/null && echo "WordPress is up on http://$HOST:$PORT" ;;
  env)
    PW=$(python3 -c "import json;print(json.load(open('$ROOT/seed.json'))['password'])")
    echo "export WP_TEST_URL=http://$HOST:$PORT WP_TEST_USER=admin WP_TEST_APP_PASSWORD=$PW DEPLOY_ALLOW_PRIVATE_HOSTS=true" ;;
  seo)
    php -r "\$_SERVER['HTTP_HOST']='$HOST:$PORT'; require '$ROOT/wp-load.php'; update_option('searchos_fake_seo', '${2:-none}');" ;;
  stop)
    pkill -f "php -S 0.0.0.0:$PORT" || true ;;
esac
