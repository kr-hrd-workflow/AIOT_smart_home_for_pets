import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
LOCAL_SETUP_E2E = ROOT / "dashboard" / "e2e" / "local-setup.spec.ts"
MOSQUITTO_ENTRYPOINT = ROOT / "infra" / "mosquitto" / "docker-entrypoint.sh"
MANIFEST = json.loads((ROOT / "tools" / "platform-manifest.json").read_text(encoding="utf-8"))
CHECKOUT = MANIFEST["managed_exact"]["actions"]["actions/checkout"]
PLANNING_SHA = "5944c4e366b764e8ffd228177eeda4858ffd3263"
JOBS = {"firmware-host", "firmware-pico", "backend-unit", "integration-live", "dashboard", "ci"}
WORK_JOBS = JOBS - {"ci"}


def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def run_text(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_ci_has_exact_pinned_jobs_and_manifest_bootstrap() -> None:
    workflow = load_workflow()
    assert set(workflow["jobs"]) == JOBS

    for name, job in workflow["jobs"].items():
        assert job["runs-on"] == "ubuntu-24.04"
        assert "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in job["steps"])
        assert job["steps"][0]["uses"] == f"actions/checkout@{CHECKOUT}"
        assert "bash tools/bootstrap_ci.sh" in job["steps"][1]["run"]
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", step["uses"])

    for name in WORK_JOBS:
        assert ".runtime/platform-linux.json" in run_text(workflow["jobs"][name])


def test_ci_commands_keep_tool_and_platform_identities_explicit() -> None:
    workflow = load_workflow()
    host = run_text(workflow["jobs"]["firmware-host"])
    pico = run_text(workflow["jobs"]["firmware-pico"])
    integration = run_text(workflow["jobs"]["integration-live"])
    dashboard = run_text(workflow["jobs"]["dashboard"])
    backend = workflow["jobs"]["backend-unit"]

    assert "tools/build_pico_host.sh" in host
    for identity in ("pico_sdk.url", "pico_sdk.commit", "pico_sdk.board", "pico_sdk.platform", "pico_sdk.resolved_platform"):
        assert identity in pico
    assert "entrance-01" in pico and "petzone-01" in pico

    assert "managed_exact.containers" in integration
    assert "compose.yml" in integration
    assert "pg_isready" in integration
    assert "databaseReady" in integration
    assert "mqttReady" in integration
    assert "compose_plugin_path" in integration
    assert integration.count("& $backendPython -m pytest") == 2
    assert "uv_path run --project backend --frozen pytest" not in integration
    assert "test_malformed_or_stale_messages_fail" in integration
    assert "test_real_postgres_mqtt_production_handlers_drive_the_full_sequence" in integration
    assert "postgres:" not in integration and "eclipse-mosquitto:" not in integration
    for image in MANIFEST["managed_exact"]["containers"].values():
        assert image not in integration

    assert "playwright-core/browsers.json" in dashboard
    assert ".runtime/playwright.json" in dashboard
    assert "executable_sha256" in dashboard
    assert "test:e2e:demo:production" in dashboard
    assert "test:e2e:connected" in dashboard

    assert backend["env"] == {"PLANNING_SHA": PLANNING_SHA, "CANDIDATE_SHA": "${{ github.sha }}"}
    assert backend["steps"][0]["with"]["fetch-depth"] == 0
    backend_text = run_text(backend)
    assert "rev-parse HEAD" in backend_text
    assert "merge-base --is-ancestor $env:PLANNING_SHA $env:CANDIDATE_SHA" in backend_text

    forbidden = re.compile(r"(?m)(?:^|[;&|]\s*|\s)(?:python3?|node|npm|git|cmake|ctest|docker|playwright)(?:\s|$)")
    for name in WORK_JOBS:
        body = "\n".join(str(step.get("run", "")) for step in workflow["jobs"][name]["steps"][2:])
        assert not forbidden.search(body), name


def test_dashboard_e2e_uses_the_frozen_backend_environment_portably() -> None:
    local_setup = LOCAL_SETUP_E2E.read_text(encoding="utf-8")

    assert "toolchain.paths?.uv_path" in local_setup
    assert '["run", "--project", backendRoot, "--frozen", "python", "-c", serverCode]' in local_setup
    assert "UV_PYTHON: managedPython" in local_setup
    assert 'UV_PYTHON_DOWNLOADS: "never"' in local_setup
    assert '".venv/Lib/site-packages"' not in local_setup


def test_mosquitto_runtime_credentials_belong_to_the_broker_user() -> None:
    entrypoint = MOSQUITTO_ENTRYPOINT.read_text(encoding="utf-8")
    assert 'chown -R mosquitto:mosquitto "$runtime"' in entrypoint


def test_aggregate_fails_when_any_required_job_fails() -> None:
    job = load_workflow()["jobs"]["ci"]
    assert job["needs"] == ["firmware-host", "firmware-pico", "backend-unit", "integration-live", "dashboard"]
    assert job["if"] == "${{ always() }}"
    assert job["steps"][2]["env"]["NEEDS_JSON"] == "${{ toJson(needs) }}"
    body = run_text(job)
    assert "result -ne 'success'" in body


def test_local_parser_rejects_floating_and_identity_mutations(monkeypatch: pytest.MonkeyPatch) -> None:
    mutations = [
        (lambda data: data["jobs"]["firmware-host"]["steps"][0].update(uses="actions/checkout@v4"),
         test_ci_has_exact_pinned_jobs_and_manifest_bootstrap),
        (lambda data: data["jobs"]["dashboard"]["steps"][2].update(run="npm test"),
         test_ci_commands_keep_tool_and_platform_identities_explicit),
        (lambda data: data["jobs"]["firmware-pico"]["steps"][2].update(
            run=data["jobs"]["firmware-pico"]["steps"][2]["run"].replace("pico_sdk.board", "pico_sdk.wrong_board")),
         test_ci_commands_keep_tool_and_platform_identities_explicit),
        (lambda data: data["jobs"]["dashboard"]["steps"][2].update(
            run=data["jobs"]["dashboard"]["steps"][2]["run"].replace(".runtime/playwright.json", ".runtime/browser.json")),
         test_ci_commands_keep_tool_and_platform_identities_explicit),
        (lambda data: data["jobs"]["integration-live"]["steps"][2].update(
            run=data["jobs"]["integration-live"]["steps"][2]["run"] + "\n# postgres:17"),
         test_ci_commands_keep_tool_and_platform_identities_explicit),
    ]
    original_loader = load_workflow
    for mutate, validator in mutations:
        changed = copy.deepcopy(original_loader())
        mutate(changed)
        monkeypatch.setattr(sys.modules[__name__], "load_workflow", lambda changed=changed: changed)
        with pytest.raises(AssertionError):
            validator()
    monkeypatch.setattr(sys.modules[__name__], "load_workflow", original_loader)


def test_non_descendant_candidate_is_rejected() -> None:
    git = Path(os.environ["PETCARE_TEST_GIT"])
    parent = subprocess.run(
        [git, "rev-parse", f"{PLANNING_SHA}^"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    result = subprocess.run(
        [git, "merge-base", "--is-ancestor", PLANNING_SHA, parent], cwd=ROOT,
    )
    assert result.returncode != 0
