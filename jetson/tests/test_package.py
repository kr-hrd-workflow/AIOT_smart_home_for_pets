import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "jetson" / "install.sh"
UNIT = ROOT / "jetson" / "petcare-vision.service"


EXPECTED_UNIT = """[Unit]
Description=PetCare Jetson Vision Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=petcare-vision
Group=petcare-vision
SupplementaryGroups=video
WorkingDirectory=/opt/petcare-vision
ExecStart=/usr/bin/python3 /opt/petcare-vision/vision_node.py --config /var/lib/petcare-vision/config.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/petcare-vision
UMask=0077
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
"""


class PackageTests(unittest.TestCase):
    def native_path(self, path):
        value = path.resolve().as_posix()
        if os.name == "nt":
            return "/mnt/%s%s" % (value[0].lower(), value[2:])
        return value

    def native_command(self, *arguments):
        return (["wsl"] if os.name == "nt" else []) + list(arguments)

    def test_unit_is_exactly_the_frozen_hardened_service(self):
        self.assertEqual(UNIT.read_text(encoding="utf-8"), EXPECTED_UNIT)

    def test_fixture_install_is_contained_and_skips_mutating_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "source"
            source.mkdir()
            shutil.copy2(INSTALLER, source / "install.sh")
            shutil.copy2(UNIT, source / "petcare-vision.service")
            for name in (
                "vision_node.py",
                "protocol.py",
                "clip_writer.py",
                "tensorrt_yolo.py",
                "model-manifest.json",
            ):
                shutil.copy2(ROOT / "jetson" / name, source / name)

            fake_bin = work / "fake-bin"
            called = work / "called"
            fixture = work / "root"
            fake_bin.mkdir()
            called.mkdir()
            for command in ("useradd", "ufw", "systemctl", "openssl"):
                path = fake_bin / command
                path.write_text(
                    "#!/bin/sh\ntouch \"$CALLED_DIR/%s\"\nexit 99\n" % command,
                    encoding="utf-8",
                )

            fake_linux = self.native_path(fake_bin)
            called_linux = self.native_path(called)
            source_linux = self.native_path(source)
            fixture_linux = self.native_path(fixture)
            subprocess.run(
                self.native_command("chmod", "+x")
                + [self.native_path(fake_bin / command) for command in ("useradd", "ufw", "systemctl", "openssl")],
                check=True,
            )
            result = subprocess.run(
                self.native_command(
                    "env",
                    "PATH=%s:/usr/bin:/bin" % fake_linux,
                    "CALLED_DIR=%s" % called_linux,
                    "bash",
                    "%s/install.sh" % source_linux,
                    "--fixture-root",
                    fixture_linux,
                ),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

            self.assertEqual(result.stdout.strip(), "Jetson vision package fixture PASS")
            self.assertEqual(list(called.iterdir()), [])
            help_result = subprocess.run(
                self.native_command("env", "-i", "PATH=/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE=1", "/usr/bin/python3", "%s/opt/petcare-vision/vision_node.py" % fixture_linux, "--help"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertEqual(
                sorted(path.relative_to(fixture).as_posix() for path in fixture.rglob("*") if path.is_file()),
                [
                    "etc/systemd/system/petcare-vision.service",
                    "opt/petcare-vision/clip_writer.py",
                    "opt/petcare-vision/model-manifest.json",
                    "opt/petcare-vision/model.engine",
                    "opt/petcare-vision/model.engine.json",
                    "opt/petcare-vision/protocol.py",
                    "opt/petcare-vision/tensorrt_yolo.py",
                    "opt/petcare-vision/vision_node.py",
                    "root/petcare-jetson-pairing.json",
                    "var/lib/petcare-vision/config.json",
                    "var/lib/petcare-vision/device.crt",
                    "var/lib/petcare-vision/device.key",
                    "var/lib/petcare-vision/psk.bin",
                ],
            )

            config = json.loads((fixture / "var/lib/petcare-vision/config.json").read_text(encoding="utf-8"))
            self.assertEqual(
                config,
                {
                    "bind_ip": "192.168.50.20",
                    "port": 9443,
                    "webcam": "/dev/video0",
                    "certificate_path": "/var/lib/petcare-vision/device.crt",
                    "private_key_path": "/var/lib/petcare-vision/device.key",
                    "psk_path": "/var/lib/petcare-vision/psk.bin",
                    "engine_path": "/opt/petcare-vision/model.engine",
                    "engine_metadata_path": "/opt/petcare-vision/model.engine.json",
                    "state_dir": "/var/lib/petcare-vision",
                    "temperature_path": "/sys/devices/virtual/thermal/thermal_zone0/temp",
                    "max_temperature_c": 80.0,
                },
            )
            bundle = json.loads((fixture / "root/petcare-jetson-pairing.json").read_text(encoding="utf-8"))
            self.assertEqual(set(bundle), {"url", "certificate_pem", "psk_base64url"})
            self.assertEqual(bundle["url"], "https://192.168.50.20:9443")
            self.assertNotIn("PRIVATE KEY", json.dumps(bundle))

            tailscale_fixture = work / "tailscale-root"
            tailscale_result = subprocess.run(
                self.native_command(
                    "env",
                    "PATH=%s:/usr/bin:/bin" % fake_linux,
                    "CALLED_DIR=%s" % called_linux,
                    "bash",
                    "%s/install.sh" % source_linux,
                    "--fixture-root",
                    self.native_path(tailscale_fixture),
                    "--bind-ip",
                    "100.64.0.10",
                    "--home-ip",
                    "100.64.0.11",
                    "--interface",
                    "tailscale0",
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            self.assertEqual(tailscale_result.returncode, 0, tailscale_result.stderr)
            tailscale_config = json.loads(
                (tailscale_fixture / "var/lib/petcare-vision/config.json").read_text(encoding="utf-8")
            )
            tailscale_bundle = json.loads(
                (tailscale_fixture / "root/petcare-jetson-pairing.json").read_text(encoding="utf-8")
            )
            self.assertEqual(tailscale_config["bind_ip"], "100.64.0.10")
            self.assertEqual(tailscale_bundle["url"], "https://100.64.0.10:9443")

            mixed_fixture = work / "mixed-root"
            mixed_result = subprocess.run(
                self.native_command(
                    "env",
                    "PATH=%s:/usr/bin:/bin" % fake_linux,
                    "CALLED_DIR=%s" % called_linux,
                    "bash",
                    "%s/install.sh" % source_linux,
                    "--fixture-root",
                    self.native_path(mixed_fixture),
                    "--bind-ip",
                    "100.64.0.10",
                    "--home-ip",
                    "192.168.50.10",
                    "--interface",
                    "tailscale0",
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            self.assertNotEqual(mixed_result.returncode, 0)
            self.assertFalse(mixed_fixture.exists())
            script = INSTALLER.read_text(encoding="utf-8")
            self.assertIn('chmod 600 "$root/var/lib/petcare-vision/config.json"', script)
            self.assertIn('os.chmod(root + "/root/petcare-jetson-pairing.json", 0o600)', script)

    def test_real_install_is_approval_gated_and_fail_closed(self):
        script = INSTALLER.read_text(encoding="utf-8")
        for guard in (
            "aarch64 required",
            "L4T R32.7.6 required",
            "TensorRT 8.2.1 required",
            "matching RFC1918 LAN or Tailscale IPv4 pair required",
            "Ethernet interface required",
            "Tailscale interface required",
            "Wi-Fi interface is forbidden",
            "webcam required",
            "temperature probe required",
            "10 W power mode required",
            "ufw required",
            "firewall must be active with default deny",
            "port 9443 already has a firewall rule",
            "exclusive firewall rule verification failed",
        ):
            self.assertIn(guard, script)
        firewall_preflight = script.rindex("\nfirewall_preflight\n")
        for first_mutation in ("groupadd --system", "useradd --system", "install -d", "openssl req"):
            self.assertLess(firewall_preflight, script.index(first_mutation))
        self.assertEqual(script.count('ufw insert 1 allow in on "$interface" from "$home_ip" to "$bind_ip" port 9443 proto tcp'), 1)
        self.assertEqual(script.count('ufw insert 1 deny in to "$bind_ip" port 9443 proto tcp'), 1)
        self.assertLess(script.index("apply_firewall"), script.index("verify_firewall"))
        self.assertLess(script.index("verify_firewall"), script.index("systemctl enable --now petcare-vision.service"))
        self.assertLess(script.index("verify_install"), script.index("systemctl enable --now petcare-vision.service"))
        self.assertNotIn("docker", script.lower())
        self.assertNotIn("pip install", script.lower())
        self.assertNotIn("cloudflared", script.lower())

    def test_firewall_policy_functions_fail_closed(self):
        script = INSTALLER.read_text(encoding="utf-8")
        functions = script[
            script.index("firewall_preflight()") : script.index('if [ "$mode" = fixture ]')
        ]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fake_bin = work / "bin"
            fake_bin.mkdir()
            ufw = fake_bin / "ufw"
            ufw.write_bytes(b'#!/bin/sh\nif [ "$1" = status ]; then printf "%s\\n" "$UFW_STATUS"; else printf "%s\\n" "$*" >>"$UFW_LOG"; fi\n')
            harness = work / "firewall.sh"
            harness.write_bytes((
                "#!/bin/sh\nset -eu\n"
                'die() { echo "$1" >&2; exit 1; }\n'
                "bind_ip=192.168.50.20\nhome_ip=192.168.50.10\ninterface=eth0\n"
                + functions
                + '\ncase "$1" in preflight) firewall_preflight ;; apply) apply_firewall ;; verify) verify_firewall ;; esac\n'
            ).encode("utf-8"))
            subprocess.run(
                self.native_command("chmod", "+x", self.native_path(ufw), self.native_path(harness)),
                check=True,
            )

            active = "Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)"

            def run(operation, status):
                return subprocess.run(
                    self.native_command(
                        "env",
                        "PATH=%s:/usr/bin:/bin" % self.native_path(fake_bin),
                        "UFW_STATUS=%s" % status,
                        "UFW_LOG=%s" % self.native_path(work / "ufw.log"),
                        "sh",
                        self.native_path(harness),
                        operation,
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                )

            result = run("preflight", active)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(run("preflight", active.replace("Status: active", "Status: inactive")).returncode, 0)
            self.assertNotEqual(run("preflight", active.replace("deny (incoming)", "allow (incoming)")).returncode, 0)
            self.assertNotEqual(run("preflight", active + "\n9443/tcp ALLOW IN Anywhere").returncode, 0)
            self.assertEqual(run("apply", active).returncode, 0)
            self.assertEqual(
                (work / "ufw.log").read_text(encoding="utf-8").splitlines(),
                [
                    "insert 1 deny in to 192.168.50.20 port 9443 proto tcp",
                    "insert 1 allow in on eth0 from 192.168.50.10 to 192.168.50.20 port 9443 proto tcp",
                ],
            )
            exact = active + "\n192.168.50.20 9443/tcp on eth0 ALLOW IN 192.168.50.10\n192.168.50.20 9443/tcp DENY IN Anywhere"
            result = run("verify", exact)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(run("verify", exact + "\n9443/tcp ALLOW IN Anywhere").returncode, 0)
            self.assertEqual(run("verify", exact + "\nAnywhere ALLOW IN 192.168.50.0/24").returncode, 0)
            self.assertEqual(run("verify", exact + "\n9000:9500/tcp ALLOW IN Anywhere").returncode, 0)
            swapped = active + "\n192.168.50.20 9443/tcp DENY IN Anywhere\n192.168.50.20 9443/tcp on eth0 ALLOW IN 192.168.50.10"
            self.assertNotEqual(run("verify", swapped).returncode, 0)


if __name__ == "__main__":
    unittest.main()
