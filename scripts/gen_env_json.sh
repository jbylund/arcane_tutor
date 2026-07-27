#!/bin/sh
# Create env.json with working local defaults, if it does not already exist.
#
# env.json is the single source of truth for a checkout's local credentials: the
# makefile reads it back into XPGUSER/XPGPASSWORD/XPGDATABASE and mirrors it into
# .env for docker compose, so both agree on one set of values.
#
# It is deliberately never overwritten. These credentials initialize the postgres
# data volume on first boot, so changing them afterwards locks the API out of an
# existing database (recover with `make reset-<env>`, which drops the volume).
set -eu

# Bytes of randomness in the generated postgres password. Hex-encoded, so the
# resulting string is twice this many characters.
PASSWORD_RANDOM_BYTES=16

# Starting values for everything else. Safe to edit in env.json before first boot.
DEFAULT_DATABASE=magic
DEFAULT_USER=foouser

target="${1:-env.json}"

if [ -e "${target}" ]; then
    exit 0
fi

password="$(
    openssl rand -hex "${PASSWORD_RANDOM_BYTES}" 2>/dev/null ||
        od -An -tx1 -N "${PASSWORD_RANDOM_BYTES}" /dev/urandom | tr -d ' \n'
)"

cat > "${target}" <<EOF
{
  "ENABLE_ENGINE": "true",
  "XPGDATABASE": "${DEFAULT_DATABASE}",
  "XPGPASSWORD": "${password}",
  "XPGUSER": "${DEFAULT_USER}"
}
EOF
