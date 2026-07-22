#!/bin/sh
set -eu

: "${PETCARE_MQTT_USERNAME:?}"
: "${PETCARE_MQTT_PASSWORD:?}"

set -- $(hostname -i)
[ "$#" -eq 1 ] || { echo 'expected one container IPv4 address' >&2; exit 1; }
container_bind_host=$1
case "$container_bind_host" in
  127.*|*:*|'') echo 'invalid container IPv4 address' >&2; exit 1 ;;
esac

runtime=/tmp/petcare-mosquitto
umask 077
mkdir -p "$runtime"
password_file="$runtime/passwords"
config_file="$runtime/mosquitto.conf"
printf '%s\n%s\n' "$PETCARE_MQTT_PASSWORD" "$PETCARE_MQTT_PASSWORD" |
  mosquitto_passwd -c "$password_file" "$PETCARE_MQTT_USERNAME"
chmod 600 "$password_file"
sed \
  -e "s/{{PORT}}/1883/" \
  -e "s/{{BIND_HOST}}/$container_bind_host/" \
  -e "s#{{PASSWORD_FILE}}#$password_file#" \
  /petcare/mosquitto.conf >"$config_file"
chown -R mosquitto:mosquitto "$runtime"
exec mosquitto -c "$config_file"
