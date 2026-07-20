import base64
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tools" / "platform-manifest.json"
WIRE_FIXTURE = ROOT / "contracts" / "petcare-agent-wire-v1.json"

FFMPEG = {
    "version": "8.1.2-22-g94138f6973",
    "source_tag": "autobuild-2026-07-19-13-12",
    "windows_x64": {
        "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-19-13-12/ffmpeg-n8.1.2-22-g94138f6973-win64-gpl-8.1.zip",
        "sha256": "9DB2860AF5D1C536ED7FCB7ED84FA4EF80D188D1396D1CDF8CAD180137510F3F",
    },
    "linux_x64": {
        "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-19-13-12/ffmpeg-n8.1.2-22-g94138f6973-linux64-gpl-8.1.tar.xz",
        "sha256": "166375E7F8B1F6963949A61A83FFFFE858EBA742F6326180B8FF3BC58B205C72",
    },
    "linux_arm64": {
        "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-19-13-12/ffmpeg-n8.1.2-22-g94138f6973-linuxarm64-gpl-8.1.tar.xz",
        "sha256": "371203E43AB3AAA703C9904B40F6A6065DCC08FD3260F441F3BBE040E1AFE8BF",
    },
}

CLOUDFLARED = {
    "version": "2026.7.2",
    "windows_x64": {
        "url": "https://github.com/cloudflare/cloudflared/releases/download/2026.7.2/cloudflared-windows-amd64.exe",
        "sha256": "CDB5D4432F6AE1595654A692A51308B69D2BF7AF961F5578D9391837CF072DF9",
    },
    "linux_x64": {
        "url": "https://github.com/cloudflare/cloudflared/releases/download/2026.7.2/cloudflared-linux-amd64",
        "sha256": "EC905EA7B7E327FF8ABDDE8CB64697A2152DE74DBCDBF6AEC9DB8364EB3886CD",
    },
    "linux_arm64": {
        "url": "https://github.com/cloudflare/cloudflared/releases/download/2026.7.2/cloudflared-linux-arm64",
        "sha256": "405DF476437E027FC6D18729A5A77155C0A33A6082AEEE60A799A688F3052E66",
    },
}

CANONICAL = """PETCARE-CLIP-V1
POST
/api/petcare/agent/clips
agent_01
camera_01
1784520000
AAAAAAAAAAAAAAAAAAAAAA
Il4ucfaWNpVoTPXCrvfVgv_3asuMAo7Yt5ycUryTSV0
2026-07-20T03:59:50.000000Z
2026-07-20T04:00:20.000000Z
bed_sensor_mismatch:7,eating:41,resting:105
"""

EXPECTED_WIRE = {
    "enrollment": {
        "request": {
            "enrollment_code": "AQEBAQEBAQEBAQEBAQEBAQ",
            "algorithm": "Ed25519",
            "public_key": "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg",
            "local_camera_id": "pc-webcam-01",
        },
        "response": {
            "status": 201,
            "body": {
                "agent_id": "agent_01",
                "camera_id": "camera_01",
                "connector_token": "fixture-only-connector-token",
            },
        },
    },
    "clip": {
        "version": "PETCARE-CLIP-V1",
        "body_base64": "bXA0LWJ5dGVz",
        "content_sha256": "Il4ucfaWNpVoTPXCrvfVgv_3asuMAo7Yt5ycUryTSV0",
        "seed": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "public_key": "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg",
        "nonce": "AAAAAAAAAAAAAAAAAAAAAA",
        "canonical": CANONICAL,
        "headers": {
            "Content-Type": "video/mp4",
            "Content-Length": "9",
            "X-PetCare-Agent-Id": "agent_01",
            "X-PetCare-Camera-Id": "camera_01",
            "X-PetCare-Timestamp": "1784520000",
            "X-PetCare-Nonce": "AAAAAAAAAAAAAAAAAAAAAA",
            "X-PetCare-Content-SHA256": "Il4ucfaWNpVoTPXCrvfVgv_3asuMAo7Yt5ycUryTSV0",
            "X-PetCare-Started-At": "2026-07-20T03:59:50.000000Z",
            "X-PetCare-Ended-At": "2026-07-20T04:00:20.000000Z",
            "X-PetCare-Events": "bed_sensor_mismatch:7,eating:41,resting:105",
            "X-PetCare-Signature": "fiTRBQk2p-2ny3LcFvBtHO2DdnqC0CqueJzuczGdC7xA_Idv0YAZ0nDCuGBiPVqS8SwldyHTrhDatHMBFUW5Aw",
        },
        "signature": "fiTRBQk2p-2ny3LcFvBtHO2DdnqC0CqueJzuczGdC7xA_Idv0YAZ0nDCuGBiPVqS8SwldyHTrhDatHMBFUW5Aw",
        "receipt": {
            "status": 201,
            "body": {
                "id": "clip_01",
                "createdAt": "2026-07-20T04:00:21.000Z",
                "expiresAt": "2026-07-27T04:00:21.000Z",
            },
        },
    },
}


def read_json(path: Path) -> dict:
    def unique_object(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = child
        return value

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def test_remote_manifest_pins_are_immutable() -> None:
    managed = read_json(MANIFEST)["managed_exact"]
    assert managed["ffmpeg"] == FFMPEG
    assert managed["cloudflared"] == CLOUDFLARED


def test_toolchain_runtime_inherits_current_manifest() -> None:
    runtime = read_json(ROOT / ".runtime" / "toolchain.json")
    assert runtime["manifest_sha256"] == hashlib.sha256(MANIFEST.read_bytes()).hexdigest().upper()


def test_one_shared_wire_fixture_is_cryptographically_exact() -> None:
    fixture = read_json(WIRE_FIXTURE)
    assert fixture == EXPECTED_WIRE

    clip = fixture["clip"]
    body = base64.urlsafe_b64decode(clip["body_base64"] + "==")
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
    assert digest == clip["content_sha256"]
    assert len(body) == int(clip["headers"]["Content-Length"])
    assert clip["signature"] == clip["headers"]["X-PetCare-Signature"]

    toolchain = read_json(ROOT / ".runtime" / "toolchain.json")
    node = Path(toolchain["paths"]["node_path"])
    assert node.is_absolute() and node.is_file()
    verifier = r"""
const {createPrivateKey, createPublicKey, sign, verify} = require('node:crypto');
const fs = require('node:fs');
const v = JSON.parse(fs.readFileSync(0, 'utf8'));
const seed = Buffer.from(v.seed, 'base64url');
const privateKey = createPrivateKey({
  key: Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'), seed]),
  format: 'der',
  type: 'pkcs8',
});
const publicKey = createPublicKey(privateKey);
const publicRaw = publicKey.export({format: 'der', type: 'spki'}).subarray(-32).toString('base64url');
const signature = sign(null, Buffer.from(v.canonical, 'utf8'), privateKey).toString('base64url');
if (publicRaw !== v.public_key || signature !== v.signature ||
    !verify(null, Buffer.from(v.canonical, 'utf8'), publicKey, Buffer.from(v.signature, 'base64url'))) {
  process.exit(1);
}
"""
    result = subprocess.run(
        [node, "-e", verifier],
        input=json.dumps(clip),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_auth_and_agent_dependencies_are_exact() -> None:
    managed = read_json(MANIFEST)["managed_exact"]
    assert managed["backend_dependencies"]["cryptography"] == "49.0.0"
    assert managed["backend_dependencies"]["pywin32"] == "312"
    sites = {
        "@supabase/ssr": "0.12.3",
        "@supabase/supabase-js": "2.110.7",
        "jose": "6.2.3",
    }
    for name, version in sites.items():
        assert managed["sites_dependencies"][name] == version
    assert managed["sites_plugin"]["version"] == "0.1.30"

    backend = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = backend["project"]["dependencies"]
    assert "cryptography==49.0.0" in dependencies
    assert "pywin32==312; sys_platform == 'win32'" in dependencies

    uv_lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text(encoding="utf-8"))
    locked = {(package["name"], package["version"]) for package in uv_lock["package"]}
    assert ("cryptography", "49.0.0") in locked
    assert ("pywin32", "312") in locked

    package = read_json(ROOT / "dashboard" / "package.json")
    package_lock = read_json(ROOT / "dashboard" / "package-lock.json")
    assert package["scripts"]["test:d1"] == (
        "vitest run tests/db tests/tenancy/repository.d1.test.ts"
    )
    for name, version in sites.items():
        assert package["dependencies"][name] == version
        assert package_lock["packages"][""]["dependencies"][name] == version
        assert package_lock["packages"][f"node_modules/{name}"]["version"] == version


def test_runtime_authority_has_no_path_fallback_or_live_secret() -> None:
    managed = read_json(MANIFEST)["managed_exact"]
    artifacts = {"ffmpeg": managed["ffmpeg"], "cloudflared": managed["cloudflared"]}
    serialized = json.dumps(artifacts, sort_keys=True).lower()
    assert "fallback" not in serialized
    assert '"path"' not in serialized
    assert "token" not in serialized

    fixture = read_json(WIRE_FIXTURE)
    assert fixture["enrollment"]["response"]["body"]["connector_token"].startswith("fixture-only-")
