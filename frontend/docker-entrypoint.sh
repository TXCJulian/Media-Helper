#!/bin/sh
set -e

BACKEND_HOST=${BACKEND_HOST:-helper-backend}
BACKEND_PORT=${BACKEND_PORT:-3332}

sed -i \
    -e "s/__BACKEND_HOST__/${BACKEND_HOST}/g" \
    -e "s/__BACKEND_PORT__/${BACKEND_PORT}/g" \
    /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
