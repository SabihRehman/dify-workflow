#!/bin/bash

HTTPS_CONFIG=''

if [ "${NGINX_HTTPS_ENABLED}" = "true" ]; then
    # Check if the certificate and key files for the specified domain exist
    if [ -n "${CERTBOT_DOMAIN}" ] && \
       [ -f "/etc/letsencrypt/live/${CERTBOT_DOMAIN}/${NGINX_SSL_CERT_FILENAME}" ] && \
       [ -f "/etc/letsencrypt/live/${CERTBOT_DOMAIN}/${NGINX_SSL_CERT_KEY_FILENAME}" ]; then
        SSL_CERTIFICATE_PATH="/etc/letsencrypt/live/${CERTBOT_DOMAIN}/${NGINX_SSL_CERT_FILENAME}"
        SSL_CERTIFICATE_KEY_PATH="/etc/letsencrypt/live/${CERTBOT_DOMAIN}/${NGINX_SSL_CERT_KEY_FILENAME}"
    else
        SSL_CERTIFICATE_PATH="/etc/ssl/${NGINX_SSL_CERT_FILENAME}"
        SSL_CERTIFICATE_KEY_PATH="/etc/ssl/${NGINX_SSL_CERT_KEY_FILENAME}"
    fi
    export SSL_CERTIFICATE_PATH
    export SSL_CERTIFICATE_KEY_PATH

    # set the HTTPS_CONFIG environment variable to the content of the https.conf.template
    HTTPS_CONFIG=$(envsubst < /etc/nginx/https.conf.template)
    export HTTPS_CONFIG
    # Substitute the HTTPS_CONFIG in the default.conf.template with content from https.conf.template
    envsubst '${HTTPS_CONFIG}' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf
fi
export HTTPS_CONFIG

if [ "${NGINX_ENABLE_CERTBOT_CHALLENGE}" = "true" ]; then
    ACME_CHALLENGE_LOCATION='location /.well-known/acme-challenge/ { root /var/www/html; }'
else
    ACME_CHALLENGE_LOCATION=''
fi
export ACME_CHALLENGE_LOCATION

# With HTTPS on, send plain-HTTP requests to HTTPS. 308 (not 301) keeps the
# method and body, so API POSTs aren't turned into GETs. Requests by IP go to
# CERTBOT_DOMAIN, which the certificate covers. ACME challenges stay on HTTP.
if [ "${NGINX_HTTPS_ENABLED}" = "true" ]; then
    HTTPS_REDIRECT_HOST="${CERTBOT_DOMAIN:-\$host}"
    HTTPS_REDIRECT="set \$https_redirect ''; if (\$scheme = http) { set \$https_redirect 1; } if (\$request_uri ~ ^/\\.well-known/acme-challenge/) { set \$https_redirect ''; } if (\$https_redirect) { return 308 https://${HTTPS_REDIRECT_HOST}\$request_uri; }"
else
    HTTPS_REDIRECT=''
fi
export HTTPS_REDIRECT

env_vars=$(printenv | cut -d= -f1 | sed 's/^/$/g' | paste -sd, -)

envsubst "$env_vars" < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
envsubst "$env_vars" < /etc/nginx/proxy.conf.template > /etc/nginx/proxy.conf

envsubst "$env_vars" < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf

# Start Nginx using the default entrypoint
exec nginx -g 'daemon off;'
