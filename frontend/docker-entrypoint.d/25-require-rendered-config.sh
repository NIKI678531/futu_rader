#!/bin/sh
set -eu

: "${API_UPSTREAM:?API_UPSTREAM must name the backend Service and port}"
rendered="${NGINX_ENVSUBST_OUTPUT_DIR:-/etc/nginx/conf.d}/default.conf"
if [ ! -s "$rendered" ] || ! grep -Fq "proxy_pass http://${API_UPSTREAM}/api/;" "$rendered"; then
    echo "FATAL: nginx runtime configuration was not rendered correctly" >&2
    exit 1
fi
nginx -t