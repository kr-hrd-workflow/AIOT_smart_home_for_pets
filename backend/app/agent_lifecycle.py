from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from weakref import WeakKeyDictionary

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.orm import Session

from .account_cleanup import ActivityCleanupRepository, ActivityCleanupWorker
from .agent_client import (
    SignedActivityCleanupClient,
    SignedClipUploadClient,
    SignedSnapshotClient,
)
from .agent_config import load_runtime_config
from .clip_delivery import ClipAdmissionWorker, ClipDeliveryWorker, utc_now
from .clip_outbox import SqlAlchemyClipOutboxRepository
from .clip_upload_queue import ClipUploadQueue
from .config import load_config
from .jetson_client import JetsonVisionClient
from .snapshot_delivery import SnapshotDeliveryWorker


__all__ = (
    "AgentLifecycleComponents",
    "build_agent_components",
    "start_agent_components",
    "stop_agent_components",
)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class AgentLifecycleComponents:
    jetson_client: JetsonVisionClient | None
    clip_admission: ClipAdmissionWorker | None
    clip_delivery: ClipDeliveryWorker | None
    upload_queue: ClipUploadQueue
    started_at: datetime


@dataclass(slots=True)
class _LifecycleState:
    started: bool = False
    stopped: bool = False
    last_error: str | None = None
    cleanup_worker: ActivityCleanupWorker | None = None
    snapshot_worker: SnapshotDeliveryWorker | None = None


_states: WeakKeyDictionary[AgentLifecycleComponents, _LifecycleState] = WeakKeyDictionary()
_states_lock = threading.Lock()


def _state_for(components: AgentLifecycleComponents) -> _LifecycleState:
    with _states_lock:
        state = _states.get(components)
        if state is None:
            state = _LifecycleState()
            _states[components] = state
        return state


def _component_health_state(components: AgentLifecycleComponents) -> tuple[str, str | None]:
    state = _state_for(components)
    with _states_lock:
        name = "running" if state.started and not state.stopped else "stopped"
        cleanup_error = (
            None if state.cleanup_worker is None else state.cleanup_worker.last_error
        )
        return name, state.last_error or cleanup_error


def _ffprobe_path(tools_path: Path) -> Path:
    try:
        payload = json.loads(Path(tools_path).read_text(encoding="utf-8"))
        value = payload["ffprobe_path"]
        path = Path(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("invalid agent tools configuration") from error
    if type(value) is not str or not path.is_absolute() or not path.is_file():
        raise ValueError("invalid agent tools configuration")
    return path


def _private_key(value: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(value + "="))


def build_agent_components(
    config_path: Path,
    tools_path: Path,
    session_factory: Callable[[], Session],
    summary_supplier: Callable[[], bytes],
    *,
    now: Callable[[], datetime] = utc_now,
) -> AgentLifecycleComponents:
    config_path = Path(config_path)
    runtime = load_runtime_config(config_path)
    app_config = load_config()
    private_key = _private_key(runtime.private_key.get_secret_value())
    upload_client = SignedClipUploadClient(
        origin=runtime.origin,
        agent_id=runtime.agent_id,
        camera_id=runtime.camera_id,
        private_key=private_key,
        now=now,
    )
    upload_queue = ClipUploadQueue.open(
        config_path.parent / "clip-upload-queue",
        upload_client,
        now=now,
    )
    jetson_client: JetsonVisionClient | None = None
    clip_admission: ClipAdmissionWorker | None = None
    clip_delivery: ClipDeliveryWorker | None = None
    if app_config.camera_source == "jetson":
        if app_config.jetson_config is None:
            raise ValueError("Jetson camera source requires runtime configuration")
        jetson_client = JetsonVisionClient(app_config.jetson_config)
        repository = SqlAlchemyClipOutboxRepository(session_factory)
        clip_admission = ClipAdmissionWorker(repository, jetson_client, now=now)
        clip_delivery = ClipDeliveryWorker(
            repository,
            jetson_client,
            upload_queue,
            work_dir=config_path.parent / "clip-delivery",
            ffprobe_path=_ffprobe_path(Path(tools_path)),
            now=now,
        )
    components = AgentLifecycleComponents(
        jetson_client,
        clip_admission,
        clip_delivery,
        upload_queue,
        now(),
    )
    cleanup_client = SignedActivityCleanupClient(
        origin=runtime.origin,
        agent_id=runtime.agent_id,
        private_key=private_key,
        now=now,
    )
    cleanup_worker = ActivityCleanupWorker(
        ActivityCleanupRepository(session_factory),
        cleanup_client,
        agent_id=runtime.agent_id,
        now=now,
    )
    snapshot_client = SignedSnapshotClient(
        origin=runtime.origin,
        agent_id=runtime.agent_id,
        private_key=private_key,
        now=now,
    )
    snapshot_worker = SnapshotDeliveryWorker(snapshot_client, summary_supplier)
    state = _state_for(components)
    state.cleanup_worker = cleanup_worker
    state.snapshot_worker = snapshot_worker
    return components


def start_agent_components(components: AgentLifecycleComponents) -> None:
    state = _state_for(components)
    with _states_lock:
        if state.started or state.stopped:
            return

    cleanup_started = False
    snapshot_started = False
    try:
        if state.cleanup_worker is not None:
            state.cleanup_worker.start()
            cleanup_started = True
        if state.snapshot_worker is not None:
            state.snapshot_worker.start()
            snapshot_started = True
        components.upload_queue.start()
        if components.jetson_client is not None:
            assert components.clip_admission is not None
            assert components.clip_delivery is not None
            try:
                components.jetson_client.calibrate_clock()
            except Exception:
                state.last_error = "jetson_unavailable"
            components.clip_admission.start()
            components.clip_delivery.start()
    except BaseException:
        if snapshot_started:
            try:
                state.snapshot_worker.stop(timeout_seconds=5.0)
            except BaseException:
                pass
        if cleanup_started:
            try:
                state.cleanup_worker.stop(timeout_seconds=5.0)
            except BaseException:
                pass
        raise
    with _states_lock:
        state.started = True


def _bounded_close(client: JetsonVisionClient, timeout_seconds: float) -> None:
    errors: list[BaseException] = []

    def close() -> None:
        try:
            client.close()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=close, name="petcare-jetson-close", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TimeoutError("Jetson client shutdown timed out")
    if errors:
        raise errors[0]


def stop_agent_components(
    components: AgentLifecycleComponents,
    *,
    timeout_seconds: float = 105.0,
) -> None:
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be nonnegative")
    state = _state_for(components)
    with _states_lock:
        if state.stopped:
            return

    deadline = time.monotonic() + timeout_seconds
    first_error: BaseException | None = None
    operations: list[tuple[float, Callable[[float], None]]] = []
    if state.cleanup_worker is not None:
        operations.append(
            (6.0, lambda timeout: state.cleanup_worker.stop(timeout_seconds=timeout))
        )
    if state.snapshot_worker is not None:
        operations.append(
            (6.0, lambda timeout: state.snapshot_worker.stop(timeout_seconds=timeout))
        )
    if components.jetson_client is not None:
        assert components.clip_admission is not None
        assert components.clip_delivery is not None
        operations.extend((
            (5.0, lambda timeout: components.clip_admission.stop(timeout_seconds=timeout)),
            (45.0, lambda timeout: components.clip_delivery.stop(timeout_seconds=timeout)),
            (2.0, lambda timeout: _bounded_close(components.jetson_client, timeout)),
        ))
    operations.append(
        (45.0, lambda timeout: components.upload_queue.stop(timeout_seconds=timeout))
    )
    for cap, operation in operations:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            operation(min(cap, remaining))
        except BaseException as error:
            first_error = first_error or error

    with _states_lock:
        state.stopped = True
        if first_error is not None and state.last_error is None:
            state.last_error = "agent_degraded"
    if first_error is not None:
        raise first_error
