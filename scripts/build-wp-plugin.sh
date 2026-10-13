#!/usr/bin/env bash
# Builds dist/searchos-connector.zip, ready to upload in WordPress (Plugins > Add New > Upload Plugin).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist
rm -f dist/searchos-connector.zip
(cd wp-plugin && zip -qr ../dist/searchos-connector.zip searchos-connector -x '*.DS_Store')
echo "dist/searchos-connector.zip"
