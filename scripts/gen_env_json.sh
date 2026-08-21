#!/bin/sh
# Create env.json with working local defaults, if it does not already exist.
#
# env.json is the single source of truth for a checkout's local credentials: the
# makefile reads it back into XPGUSER/XPGPASSWORD/XPGDATABASE and mirrors it into
# .env for docker compose, so both agree on one set of values.
#
# The postgres credentials are deliberately never overwritten: they initialize the
# postgres data volume on first boot, so changing them afterwards locks the API out
# of an existing database (recover with `make reset-<env>`, which drops the volume).
# ADMIN_PASSWORD carries no such constraint -- nothing persists it outside this file --
# so an existing env.json from before it existed gets it backfilled in place below,
# rather than only generating it for a brand new file.
set -eu

# Bytes of randomness in each generated secret. Hex-encoded, so the resulting string
# is twice this many characters. ADMIN_PASSWORD is longer because it crosses the
# network (Basic Auth on the _admin mount); the postgres password never leaves the
# compose network.
PASSWORD_RANDOM_BYTES=16
ADMIN_PASSWORD_RANDOM_BYTES=24

# Starting values for everything else. Safe to edit in env.json before first boot.
DEFAULT_DATABASE=magic
DEFAULT_USER=foouser

target="${1:-env.json}"

random_hex() {
    openssl rand -hex "$1" 2>/dev/null ||
        od -An -tx1 -N "$1" /dev/urandom | tr -d ' \n'
}

if [ -e "${target}" ]; then
    if ! jq -e 'has("ADMIN_PASSWORD")' "${target}" >/dev/null 2>&1; then
        admin_password="$(random_hex "${ADMIN_PASSWORD_RANDOM_BYTES}")"
        tmp="$(mktemp "${target}.XXXXXX")"
        jq --arg pw "${admin_password}" '. + {ADMIN_PASSWORD: $pw}' "${target}" > "${tmp}"
        mv "${tmp}" "${target}"
    fi
    exit 0
fi

password="$(random_hex "${PASSWORD_RANDOM_BYTES}")"
admin_password="$(random_hex "${ADMIN_PASSWORD_RANDOM_BYTES}")"

cat > "${target}" <<EOF
{
  "ADMIN_PASSWORD": "${admin_password}",
  "ENABLE_ENGINE": "true",
  "XPGDATABASE": "${DEFAULT_DATABASE}",
  "XPGPASSWORD": "${password}",
  "XPGUSER": "${DEFAULT_USER}"
}
EOF
