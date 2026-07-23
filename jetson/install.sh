#!/bin/sh
set -eu
umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mode=
fixture_root=
bind_ip=
home_ip=
interface=
webcam=

die() {
    echo "$1" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --fixture-root|--bind-ip|--home-ip|--interface|--webcam)
            [ "$#" -ge 2 ] || die "missing value for $1"
            case "$1" in
                --fixture-root) [ -z "$fixture_root" ] || die "duplicate argument"; fixture_root=$2 ;;
                --bind-ip) [ -z "$bind_ip" ] || die "duplicate argument"; bind_ip=$2 ;;
                --home-ip) [ -z "$home_ip" ] || die "duplicate argument"; home_ip=$2 ;;
                --interface) [ -z "$interface" ] || die "duplicate argument"; interface=$2 ;;
                --webcam) [ -z "$webcam" ] || die "duplicate argument"; webcam=$2 ;;
            esac
            shift 2
            ;;
        --install)
            [ -z "$mode" ] || die "duplicate mode"
            mode=install
            shift
            ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ -z "$fixture_root" ] || [ -z "$mode" ] || die "choose fixture or install mode"
if [ -n "$fixture_root" ]; then
    mode=fixture
    [ -n "$bind_ip" ] || bind_ip=192.168.50.20
    [ -n "$home_ip" ] || home_ip=192.168.50.10
    [ -n "$interface" ] || interface=eth0
    [ -n "$webcam" ] || webcam=/dev/video0
fi
[ -n "$mode" ] || die "use --fixture-root or --install"

classify_private_transport() {
    python3 - "$1" "$2" <<'PY'
import ipaddress
import sys

try:
    jetson = ipaddress.ip_address(sys.argv[1])
    home = ipaddress.ip_address(sys.argv[2])
except ValueError:
    raise SystemExit(1)
lan = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
tailscale = ipaddress.ip_network("100.64.0.0/10")
if jetson.version != 4 or home.version != 4 or jetson == home:
    raise SystemExit(1)
if any(jetson in network for network in lan) and any(home in network for network in lan):
    print("lan")
elif jetson in tailscale and home in tailscale:
    print("tailscale")
else:
    raise SystemExit(1)
PY
}

write_config() {
    root=$1
    cat >"$root/var/lib/petcare-vision/config.json" <<EOF
{"bind_ip":"$bind_ip","port":9443,"webcam":"$webcam","certificate_path":"/var/lib/petcare-vision/device.crt","private_key_path":"/var/lib/petcare-vision/device.key","psk_path":"/var/lib/petcare-vision/psk.bin","engine_path":"/opt/petcare-vision/model.engine","engine_metadata_path":"/opt/petcare-vision/model.engine.json","state_dir":"/var/lib/petcare-vision","temperature_path":"/sys/devices/virtual/thermal/thermal_zone0/temp","max_temperature_c":80.0}
EOF
    chmod 600 "$root/var/lib/petcare-vision/config.json"
}

write_pairing_bundle() {
    root=$1
    ROOT=$root BIND_IP=$bind_ip python3 - <<'PY'
import base64
import json
import os

root = os.environ["ROOT"]
with open(root + "/var/lib/petcare-vision/device.crt", "r", encoding="ascii") as handle:
    certificate = handle.read()
with open(root + "/var/lib/petcare-vision/psk.bin", "rb") as handle:
    psk = base64.urlsafe_b64encode(handle.read()).rstrip(b"=").decode("ascii")
with open(root + "/root/petcare-jetson-pairing.json", "w", encoding="utf-8") as handle:
    json.dump(
        {
            "url": "https://%s:9443" % os.environ["BIND_IP"],
            "certificate_pem": certificate,
            "psk_base64url": psk,
        },
        handle,
        sort_keys=True,
        separators=(",", ":"),
    )
    handle.write("\n")
os.chmod(root + "/root/petcare-jetson-pairing.json", 0o600)
PY
}

copy_runtime() {
    root=$1
    for name in vision_node.py protocol.py clip_writer.py tensorrt_yolo.py model-manifest.json; do
        [ -f "$script_dir/$name" ] && [ ! -L "$script_dir/$name" ] || die "missing runtime file: $name"
        cp "$script_dir/$name" "$root/opt/petcare-vision/$name"
        chmod 600 "$root/opt/petcare-vision/$name"
    done
    cp "$script_dir/petcare-vision.service" "$root/etc/systemd/system/petcare-vision.service"
    chmod 644 "$root/etc/systemd/system/petcare-vision.service"
}

firewall_preflight() {
    command -v ufw >/dev/null 2>&1 || die "ufw required"
    firewall_status=$(LC_ALL=C ufw status verbose)
    printf '%s\n' "$firewall_status" | grep -Fxq 'Status: active' || die "firewall must be active with default deny"
    printf '%s\n' "$firewall_status" | grep -Eq '^Default: deny \(incoming\),' || die "firewall must be active with default deny"
    if printf '%s\n' "$firewall_status" | grep -Eq '(^|[[:space:]])9443(/tcp|[[:space:]])'; then
        die "port 9443 already has a firewall rule"
    fi
}

apply_firewall() {
    ufw insert 1 deny in to "$bind_ip" port 9443 proto tcp
    ufw insert 1 allow in on "$interface" from "$home_ip" to "$bind_ip" port 9443 proto tcp
}

verify_firewall() {
    firewall_status=$(LC_ALL=C ufw status verbose)
    printf '%s\n' "$firewall_status" | grep -Fxq 'Status: active' || die "exclusive firewall rule verification failed"
    printf '%s\n' "$firewall_status" | grep -Eq '^Default: deny \(incoming\),' || die "exclusive firewall rule verification failed"
    firewall_rules=$(printf '%s\n' "$firewall_status" | grep -E '(^|[[:space:]])9443(/tcp|[[:space:]])' || true)
    [ "$(printf '%s\n' "$firewall_rules" | sed '/^$/d' | wc -l)" -eq 2 ] || die "exclusive firewall rule verification failed"
    allow_rule=$(printf '%s\n' "$firewall_rules" | sed -n '1p')
    deny_rule=$(printf '%s\n' "$firewall_rules" | sed -n '2p')
    printf '%s\n' "$allow_rule" | grep -Fq "$bind_ip" || die "exclusive firewall rule verification failed"
    printf '%s\n' "$allow_rule" | grep -Fq "$home_ip" || die "exclusive firewall rule verification failed"
    printf '%s\n' "$allow_rule" | grep -Fq "on $interface" || die "exclusive firewall rule verification failed"
    printf '%s\n' "$allow_rule" | grep -Fq 'ALLOW IN' || die "exclusive firewall rule verification failed"
    printf '%s\n' "$deny_rule" | grep -Fq "$bind_ip" || die "exclusive firewall rule verification failed"
    printf '%s\n' "$deny_rule" | grep -Fq 'DENY IN' || die "exclusive firewall rule verification failed"
}

if [ "$mode" = fixture ]; then
    [ "$fixture_root" != / ] || die "unsafe fixture root"
    [ ! -e "$fixture_root" ] || die "fixture root already exists"
    transport=$(classify_private_transport "$bind_ip" "$home_ip") || die "matching RFC1918 LAN or Tailscale IPv4 pair required"
    [ "$transport" != tailscale ] || [ "$interface" = tailscale0 ] || die "Tailscale interface required"
    [ "$transport" != lan ] || [ "$interface" != tailscale0 ] || die "Ethernet interface required"
    mkdir -p \
        "$fixture_root/etc/systemd/system" \
        "$fixture_root/opt/petcare-vision" \
        "$fixture_root/root" \
        "$fixture_root/var/lib/petcare-vision"
    chmod 700 "$fixture_root/opt/petcare-vision" "$fixture_root/root" "$fixture_root/var/lib/petcare-vision"
    copy_runtime "$fixture_root"
    printf 'fixture-engine\n' >"$fixture_root/opt/petcare-vision/model.engine"
    printf '{"fixture":true}\n' >"$fixture_root/opt/petcare-vision/model.engine.json"
    printf '%s\n' '-----BEGIN CERTIFICATE-----' 'RklYVFVSRQ==' '-----END CERTIFICATE-----' >"$fixture_root/var/lib/petcare-vision/device.crt"
    printf '%s\n' 'fixture-private-key-not-for-runtime' >"$fixture_root/var/lib/petcare-vision/device.key"
    head -c 32 /dev/urandom >"$fixture_root/var/lib/petcare-vision/psk.bin"
    chmod 600 \
        "$fixture_root/opt/petcare-vision/model.engine" \
        "$fixture_root/opt/petcare-vision/model.engine.json" \
        "$fixture_root/var/lib/petcare-vision/device.crt" \
        "$fixture_root/var/lib/petcare-vision/device.key" \
        "$fixture_root/var/lib/petcare-vision/psk.bin"
    write_config "$fixture_root"
    write_pairing_bundle "$fixture_root"
    echo "Jetson vision package fixture PASS"
    exit 0
fi

[ "$(id -u)" -eq 0 ] || die "root required"
[ -n "$bind_ip" ] && [ -n "$home_ip" ] && [ -n "$interface" ] && [ -n "$webcam" ] || die "install arguments required"
transport=$(classify_private_transport "$bind_ip" "$home_ip") || die "matching RFC1918 LAN or Tailscale IPv4 pair required"
[ "$(uname -m)" = aarch64 ] || die "aarch64 required"
grep -q '^# R32 (release), REVISION: 7\.6,' /etc/nv_tegra_release || die "L4T R32.7.6 required"
trt_version=$(dpkg-query -W -f='${Version}' tensorrt 2>/dev/null || true)
case "$trt_version" in 8.2.1*) ;; *) die "TensorRT 8.2.1 required" ;; esac
case "$transport" in
    lan)
        case "$interface" in *[!A-Za-z0-9_.:-]*|'') die "Ethernet interface required" ;; esac
        [ "$interface" != tailscale0 ] || die "Ethernet interface required"
        [ -d "/sys/class/net/$interface" ] && [ "$(cat "/sys/class/net/$interface/type")" = 1 ] || die "Ethernet interface required"
        [ ! -d "/sys/class/net/$interface/wireless" ] || die "Wi-Fi interface is forbidden"
        ;;
    tailscale)
        [ "$interface" = tailscale0 ] || die "Tailscale interface required"
        [ -d /sys/class/net/tailscale0 ] && [ "$(cat /sys/class/net/tailscale0/type)" = 65534 ] || die "Tailscale interface required"
        ;;
esac
ip -4 -o addr show dev "$interface" | grep -Eq "[[:space:]]inet[[:space:]]$bind_ip/" || die "bind IP is not assigned to interface"
[ -c "$webcam" ] && [ ! -L "$webcam" ] || die "webcam required"
temperature_path=/sys/devices/virtual/thermal/thermal_zone0/temp
[ -r "$temperature_path" ] && [ ! -L "$temperature_path" ] || die "temperature probe required"
command -v nvpmodel >/dev/null 2>&1 || die "10 W power mode required"
nvpmodel -q 2>&1 | grep -Eq 'NV Power Mode:[[:space:]]*(MAXN|10W)' || die "10 W power mode required"
for name in vision_node.py protocol.py clip_writer.py tensorrt_yolo.py model-manifest.json model.engine model.engine.json petcare-vision.service; do
    [ -f "$script_dir/$name" ] && [ ! -L "$script_dir/$name" ] || die "missing runtime file: $name"
done

firewall_preflight

getent group petcare-vision >/dev/null 2>&1 || groupadd --system petcare-vision
id petcare-vision >/dev/null 2>&1 || useradd --system --gid petcare-vision --groups video --home-dir /var/lib/petcare-vision --shell /usr/sbin/nologin petcare-vision
usermod -a -G video petcare-vision
install -d -o petcare-vision -g petcare-vision -m 0700 /opt/petcare-vision /var/lib/petcare-vision
install -d -o root -g root -m 0700 /root
copy_runtime ""
install -o petcare-vision -g petcare-vision -m 0600 "$script_dir/model.engine" /opt/petcare-vision/model.engine
install -o petcare-vision -g petcare-vision -m 0600 "$script_dir/model.engine.json" /opt/petcare-vision/model.engine.json
chown -R petcare-vision:petcare-vision /opt/petcare-vision
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
    -subj "/CN=petcare-jetson" -addext "subjectAltName=IP:$bind_ip" \
    -keyout /var/lib/petcare-vision/device.key -out /var/lib/petcare-vision/device.crt >/dev/null 2>&1
head -c 32 /dev/urandom >/var/lib/petcare-vision/psk.bin
chmod 600 /var/lib/petcare-vision/device.crt /var/lib/petcare-vision/device.key /var/lib/petcare-vision/psk.bin
write_config ""
chown -R petcare-vision:petcare-vision /var/lib/petcare-vision
write_pairing_bundle ""
apply_firewall
verify_firewall
systemctl daemon-reload

verify_install() {
    [ "$(stat -c %a /var/lib/petcare-vision)" = 700 ]
    [ "$(stat -c %a /var/lib/petcare-vision/config.json)" = 600 ]
    [ "$(stat -c %s /var/lib/petcare-vision/psk.bin)" = 32 ]
    [ "$(stat -c %a /root/petcare-jetson-pairing.json)" = 600 ]
    openssl x509 -in /var/lib/petcare-vision/device.crt -noout -checkend 1 >/dev/null
    openssl x509 -in /var/lib/petcare-vision/device.crt -noout -ext subjectAltName | grep -Fq "IP Address:$bind_ip"
    systemd-analyze verify /etc/systemd/system/petcare-vision.service
}

verify_install
systemctl enable --now petcare-vision.service
echo "Jetson vision package install PASS"
