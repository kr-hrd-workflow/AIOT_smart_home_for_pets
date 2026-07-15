from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).parents[2]
MODEL = ROOT / ".runtime" / "models" / "yolo11n.pt"
SIZE = 5_613_764
SHA256 = "0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1"


def test_provisioner_rejects_wrong_size_and_hash_and_cleans_partial():
    scratch = ROOT / ".runtime" / "vision-smoke" / "provision-test"
    model_dir = ROOT / ".runtime" / "models" / "provision-test"
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.rmtree(model_dir, ignore_errors=True)
    scratch.mkdir(parents=True)
    bad = scratch / "bad.pt"
    bad.write_bytes(b"wrong")
    command = [
        "powershell",
        "-NoProfile",
        "-File",
        str(ROOT / "tools" / "provision_vision_model.ps1"),
        "-SourcePath",
        str(bad),
        "-ModelDirectory",
        str(model_dir),
    ]
    manifest = json.loads((ROOT / "tools" / "platform-manifest.json").read_text())
    manifest["managed_exact"]["model"]["url"] = "https://example.invalid/yolo11n.pt"
    wrong_manifest = scratch / "wrong-manifest.json"
    wrong_manifest.write_text(json.dumps(manifest))
    result = subprocess.run(
        command + ["-ManifestPath", str(wrong_manifest)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "pin mismatch" in result.stderr
    assert not model_dir.exists()

    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode != 0
    assert "size mismatch" in result.stderr
    assert not (model_dir / "yolo11n.pt").exists()
    assert not (model_dir / "yolo11n.pt.partial").exists()

    bad.write_bytes(b"\0" * SIZE)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode != 0
    assert "hash mismatch" in result.stderr
    assert not (model_dir / "yolo11n.pt").exists()
    assert not (model_dir / "yolo11n.pt.partial").exists()
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.rmtree(model_dir, ignore_errors=True)
    scratch.parent.rmdir()


@pytest.mark.model_smoke
def test_real_cpu_file_source_model_smoke_deletes_scratch():
    if not MODEL.exists():
        pytest.skip("provision yolo11n.pt with tools/provision_vision_model.ps1")
    assert MODEL.stat().st_size == SIZE
    assert hashlib.sha256(MODEL.read_bytes()).hexdigest().upper() == SHA256

    import cv2
    from ultralytics import YOLO

    scratch = ROOT / ".runtime" / "vision-smoke"
    image = scratch / "input.png"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:, :, 1] = np.arange(640, dtype=np.uint8)
        assert cv2.imwrite(str(image), frame)
        result = YOLO(str(MODEL)).predict(source=str(image), device="cpu", save=False, verbose=False)
        assert len(result) == 1
        assert tuple(result[0].orig_shape) == (480, 640)
        names = set(result[0].names.values())
        assert {"person", "dog", "cat"} <= names
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    assert not scratch.exists()
