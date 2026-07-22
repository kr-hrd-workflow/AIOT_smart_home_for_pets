from __future__ import annotations

import time
from concurrent.futures import CancelledError, TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from typing import Annotated, Callable, Iterable

from fastapi import APIRouter, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders

from .contracts import (
    AnomalyEventOut,
    ApiError,
    BedCalibrationSuccess,
    BedStatus,
    BehaviorEventOut,
    CameraStatus,
    DashboardSummary,
    DeviceOut,
    HealthOut,
    SensorReadingOut,
    ZoneIn,
    ZoneOut,
)
from .events import CalibrateBedCommand
from .models import AnomalyEvent, BehaviorEvent, Device, SensorReading, Zone
from .rule_worker import RuleQueueUnavailable
from .rules import BedCalibrationRejected
from .vision import CameraUnavailable


DEFAULT_ALLOWED_ORIGINS = ("http://127.0.0.1:3000", "http://localhost:3000")
ALLOWED_METHODS = "GET,POST,PUT,OPTIONS"
ALLOWED_HEADERS = "Content-Type"
CALIBRATION_WAIT_SECONDS = 15.0
SENSOR_ORDER = (
    "temperature",
    "humidity",
    "presence_moving",
    "presence_stationary",
    "food_weight",
    "water_weight",
    "bed_pressure_left",
    "bed_pressure_center",
    "bed_pressure_right",
)


class DatabaseUnavailableError(RuntimeError):
    pass


class WorkerUnavailableError(RuntimeError):
    pass


class OriginPolicyMiddleware:
    def __init__(self, app: object, *, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)
        if not self.allowed_origins or "*" in self.allowed_origins:
            raise ValueError("an explicit non-wildcard Origin allowlist is required")

    async def __call__(self, scope: dict[str, object], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        method = str(scope["method"])
        if method == "OPTIONS" and origin is not None:
            requested_method = headers.get("access-control-request-method")
            requested_headers = headers.get("access-control-request-headers", "")
            header_names = [item.strip().lower() for item in requested_headers.split(",") if item.strip()]
            valid = (
                origin in self.allowed_origins
                and requested_method in {"GET", "POST", "PUT"}
                and all(item == "content-type" for item in header_names)
            )
            if not valid:
                await _origin_forbidden()(scope, receive, send)
                return
            response = Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": ALLOWED_METHODS,
                    "Access-Control-Allow-Headers": ALLOWED_HEADERS,
                    "Vary": "Origin",
                },
            )
            await response(scope, receive, send)
            return
        if origin is not None and origin not in self.allowed_origins:
            await _origin_forbidden()(scope, receive, send)
            return
        if origin is None:
            await self.app(scope, receive, send)
            return

        async def send_with_origin(message: dict[str, object]) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["Access-Control-Allow-Origin"] = origin
                response_headers["Vary"] = "Origin"
            await send(message)

        await self.app(scope, receive, send_with_origin)


def _origin_forbidden() -> JSONResponse:
    error = ApiError(code="origin_forbidden", message="Origin is not allowed")
    return JSONResponse(status_code=403, content=error.model_dump(mode="json"))


def _api_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ApiError(code=code, message=message).model_dump(mode="json"),
    )


def _query_error(request: Request, allowed: frozenset[str] = frozenset()) -> JSONResponse | None:
    keys = [key for key, _value in request.query_params.multi_items()]
    if any(key not in allowed for key in keys) or len(keys) != len(set(keys)):
        return _api_error(422, "validation_error", "Request validation failed")
    return None


def _parse_limit(value: str) -> int | None:
    if (
        not value
        or len(value) > 3
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
    ):
        return None
    parsed = int(value)
    return parsed if 1 <= parsed <= 500 else None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _session(application: FastAPI) -> Session:
    try:
        return application.state.session_factory()
    except RuntimeError as error:
        raise DatabaseUnavailableError("database is unavailable") from error


def _devices(session: Session) -> list[DeviceOut]:
    rows = session.execute(select(Device).order_by(Device.device_id)).scalars().all()
    return [
        DeviceOut(device_id=row.device_id, status=row.status, last_seen_at=_aware(row.last_seen_at))
        for row in rows
    ]


def _latest_sensors(session: Session) -> list[SensorReadingOut]:
    ranked = select(
        SensorReading.id.label("id"),
        SensorReading.device_id.label("device_id"),
        SensorReading.sensor_type.label("sensor_type"),
        SensorReading.value_number.label("value_number"),
        SensorReading.value_boolean.label("value_boolean"),
        SensorReading.unit.label("unit"),
        SensorReading.observed_at.label("observed_at"),
        func.row_number()
        .over(
            partition_by=(SensorReading.device_id, SensorReading.sensor_type),
            order_by=(SensorReading.observed_at.desc(), SensorReading.id.desc()),
        )
        .label("position"),
    ).subquery()
    device_order = case((ranked.c.device_id == "entrance-01", 0), else_=1)
    sensor_order = case(
        *[(ranked.c.sensor_type == sensor_type, index) for index, sensor_type in enumerate(SENSOR_ORDER)],
        else_=len(SENSOR_ORDER),
    )
    rows = session.execute(
        select(ranked)
        .where(ranked.c.position == 1)
        .order_by(device_order, sensor_order)
    ).mappings()
    output: list[SensorReadingOut] = []
    for row in rows:
        if row.sensor_type in {"presence_moving", "presence_stationary"}:
            value = bool(row.value_boolean)
        elif row.sensor_type.startswith("bed_pressure_"):
            value = int(row.value_number)
        else:
            number = float(row.value_number)
            value = int(number) if number.is_integer() else number
        output.append(
            SensorReadingOut(
                id=row.id,
                device_id=row.device_id,
                sensor_type=row.sensor_type,
                value=value,
                unit=row.unit,
                observed_at=_aware(row.observed_at),
            )
        )
    return output


def _behaviors(session: Session, limit: int) -> list[BehaviorEventOut]:
    rows = session.execute(
        select(BehaviorEvent)
        .order_by(BehaviorEvent.started_at.desc(), BehaviorEvent.id.desc())
        .limit(limit)
    ).scalars()
    return [
        BehaviorEventOut(
            id=row.id,
            subject_id=row.subject_id,
            behavior_type=row.behavior_type,
            started_at=_aware(row.started_at),
            ended_at=_aware(row.ended_at),
            duration_seconds=row.duration_seconds,
        )
        for row in rows
    ]


def _anomalies(session: Session, limit: int) -> list[AnomalyEventOut]:
    rows = session.execute(
        select(AnomalyEvent)
        .order_by(AnomalyEvent.occurred_at.desc(), AnomalyEvent.id.desc())
        .limit(limit)
    ).scalars()
    return [
        AnomalyEventOut(
            id=row.id,
            subject_id=row.subject_id,
            anomaly_type=row.anomaly_type,
            severity=row.severity,
            mismatch_kind=row.mismatch_kind,
            message=row.message,
            occurred_at=_aware(row.occurred_at),
        )
        for row in rows
    ]


def _zones(session: Session) -> list[ZoneOut]:
    ordering = case((Zone.zone_name == "food_bowl", 0), else_=1)
    rows = session.execute(select(Zone).order_by(ordering)).scalars()
    return [
        ZoneOut(
            zone_name=row.zone_name,
            x1=row.x1,
            y1=row.y1,
            x2=row.x2,
            y2=row.y2,
            enabled=row.enabled,
            updated_at=_aware(row.updated_at),
        )
        for row in rows
    ]


def _bed_status(application: FastAPI) -> BedStatus:
    snapshot = application.state.rule_engine.bed_status_snapshot
    if snapshot is None:
        raise WorkerUnavailableError("bed snapshot is unavailable")
    return snapshot.model_copy(deep=True)


def build_health(application: FastAPI, *, database_up: bool | None = None) -> HealthOut:
    if database_up is None:
        session: Session | None = None
        try:
            session = _session(application)
            session.execute(select(1)).scalar_one()
            database_up = True
        except Exception:
            database_up = False
        finally:
            if session is not None:
                session.close()
    mqtt = application.state.mqtt_ingestor
    mqtt_state = "disabled" if not mqtt.enabled else ("up" if mqtt.connected else "down")
    camera_state = application.state.camera_service.status.state
    ingress_full = application.state.rule_ingress.queue_full
    hub_full = application.state.dashboard_hub.queue_full
    worker_thread = application.state.rule_worker.thread
    worker_running = worker_thread is not None and worker_thread.is_alive()
    healthy = (
        database_up
        and mqtt_state in {"up", "disabled"}
        and camera_state == "online"
        and not ingress_full
        and not hub_full
        and worker_running
    )
    return HealthOut(
        status="healthy" if healthy else "degraded",
        database="up" if database_up else "down",
        mqtt=mqtt_state,
        camera=camera_state,
        queue="full" if ingress_full or hub_full else "ok",
        worker="running" if worker_running else "stopped",
    )


def build_dashboard_summary(application: FastAPI) -> DashboardSummary:
    session: Session | None = None
    try:
        session = _session(application)
        session.execute(select(1)).scalar_one()
        return DashboardSummary(
            generated_at=application.state.clock.utc_now(),
            health=build_health(application, database_up=True),
            devices=_devices(session),
            latest_sensors=_latest_sensors(session),
            camera=application.state.camera_service.status,
            bed=_bed_status(application),
            behaviors=_behaviors(session, 100),
            anomalies=_anomalies(session, 100),
        )
    finally:
        if session is not None:
            session.close()


def _database_call(application: FastAPI, operation: Callable[[Session], object]) -> object:
    session: Session | None = None
    try:
        session = _session(application)
        return operation(session)
    except SQLAlchemyError:
        if session is not None:
            session.rollback()
        raise
    finally:
        if session is not None:
            session.close()


def _overlap(first: Zone, second: Zone) -> bool:
    return (
        first.enabled
        and second.enabled
        and first.x1 < second.x2
        and second.x1 < first.x2
        and first.y1 < second.y2
        and second.y1 < first.y2
    )


def install_api(application: FastAPI, *, allowed_origins: Iterable[str] = DEFAULT_ALLOWED_ORIGINS) -> None:
    origins = tuple(allowed_origins)
    if not origins or "*" in origins:
        raise ValueError("an explicit non-wildcard Origin allowlist is required")
    application.add_middleware(OriginPolicyMiddleware, allowed_origins=origins)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return _api_error(422, "validation_error", "Request validation failed")

    router = APIRouter()

    @router.get("/api/health", response_model=HealthOut)
    def get_health(request: Request) -> HealthOut | JSONResponse:
        if error := _query_error(request):
            return error
        return build_health(request.app)

    @router.get("/api/dashboard/summary", response_model=DashboardSummary)
    def get_summary(request: Request) -> DashboardSummary | JSONResponse:
        if error := _query_error(request):
            return error
        try:
            return build_dashboard_summary(request.app)
        except (SQLAlchemyError, DatabaseUnavailableError):
            return _api_error(503, "database_unavailable", "Database is unavailable")
        except WorkerUnavailableError:
            return _api_error(503, "worker_unavailable", "Rule worker is unavailable")

    @router.get("/api/devices", response_model=list[DeviceOut])
    def get_devices(request: Request) -> list[DeviceOut] | JSONResponse:
        if error := _query_error(request):
            return error
        try:
            return _database_call(request.app, _devices)
        except (SQLAlchemyError, DatabaseUnavailableError):
            return _api_error(503, "database_unavailable", "Database is unavailable")

    @router.get("/api/sensors/latest", response_model=list[SensorReadingOut])
    def get_latest_sensors(request: Request) -> list[SensorReadingOut] | JSONResponse:
        if error := _query_error(request):
            return error
        try:
            return _database_call(request.app, _latest_sensors)
        except (SQLAlchemyError, DatabaseUnavailableError):
            return _api_error(503, "database_unavailable", "Database is unavailable")

    @router.get("/api/behaviors", response_model=list[BehaviorEventOut])
    def get_behaviors(
        request: Request,
        limit: Annotated[str, Query()] = "100",
    ) -> list[BehaviorEventOut] | JSONResponse:
        if error := _query_error(request, frozenset({"limit"})):
            return error
        parsed_limit = _parse_limit(limit)
        if parsed_limit is None:
            return _api_error(422, "validation_error", "Request validation failed")
        try:
            return _database_call(request.app, lambda session: _behaviors(session, parsed_limit))
        except (SQLAlchemyError, DatabaseUnavailableError):
            return _api_error(503, "database_unavailable", "Database is unavailable")

    @router.get("/api/anomalies", response_model=list[AnomalyEventOut])
    def get_anomalies(
        request: Request,
        limit: Annotated[str, Query()] = "100",
    ) -> list[AnomalyEventOut] | JSONResponse:
        if error := _query_error(request, frozenset({"limit"})):
            return error
        parsed_limit = _parse_limit(limit)
        if parsed_limit is None:
            return _api_error(422, "validation_error", "Request validation failed")
        try:
            return _database_call(request.app, lambda session: _anomalies(session, parsed_limit))
        except (SQLAlchemyError, DatabaseUnavailableError):
            return _api_error(503, "database_unavailable", "Database is unavailable")

    @router.get("/api/camera/status", response_model=CameraStatus)
    def get_camera_status(request: Request) -> CameraStatus | JSONResponse:
        if error := _query_error(request):
            return error
        return request.app.state.camera_service.status

    @router.get("/api/video_feed", response_model=None)
    def get_video_feed(request: Request) -> StreamingResponse | JSONResponse:
        if error := _query_error(request):
            return error
        camera = request.app.state.camera_service
        try:
            first = camera.mjpeg_chunk()
        except CameraUnavailable:
            return _api_error(503, "camera_unavailable", "Camera is unavailable")

        def chunks():
            yield first
            while True:
                time.sleep(0.05)
                try:
                    yield camera.mjpeg_chunk()
                except CameraUnavailable:
                    return

        return StreamingResponse(chunks(), media_type="multipart/x-mixed-replace; boundary=frame")

    @router.get("/api/bed/status", response_model=BedStatus)
    def get_bed_status(
        request: Request,
        device_id: Annotated[str, Query()] = "petzone-01",
    ) -> BedStatus | JSONResponse:
        if error := _query_error(request, frozenset({"device_id"})):
            return error
        if device_id != "petzone-01":
            return _api_error(422, "validation_error", "Request validation failed")
        try:
            return _bed_status(request.app)
        except WorkerUnavailableError:
            return _api_error(503, "worker_unavailable", "Rule worker is unavailable")

    @router.post("/api/bed/calibration", response_model=BedCalibrationSuccess)
    async def calibrate_bed(
        command: CalibrateBedCommand,
        request: Request,
    ) -> BedCalibrationSuccess | JSONResponse:
        if error := _query_error(request):
            return error
        worker = request.app.state.rule_worker
        if worker.thread is None or not worker.thread.is_alive():
            return _api_error(503, "worker_unavailable", "Rule worker is unavailable")
        try:
            future = worker.submit(command)
        except RuleQueueUnavailable:
            return _api_error(503, "queue_unavailable", "Rule queue is unavailable")
        try:
            return await run_in_threadpool(lambda: future.result(timeout=CALIBRATION_WAIT_SECONDS))
        except BedCalibrationRejected as error:
            return JSONResponse(status_code=409, content=error.error.model_dump(mode="json"))
        except (SQLAlchemyError, DatabaseUnavailableError):
            return _api_error(503, "database_unavailable", "Database is unavailable")
        except FutureTimeoutError:
            future.cancel()
            return _api_error(503, "worker_unavailable", "Rule worker is unavailable")
        except (CancelledError, RuntimeError):
            return _api_error(503, "worker_unavailable", "Rule worker is unavailable")

    @router.get("/api/zones", response_model=list[ZoneOut])
    def get_zones(request: Request) -> list[ZoneOut] | JSONResponse:
        if error := _query_error(request):
            return error
        try:
            return _database_call(request.app, _zones)
        except (SQLAlchemyError, DatabaseUnavailableError):
            return _api_error(503, "database_unavailable", "Database is unavailable")

    @router.put("/api/zones/{zone_name}", response_model=ZoneOut)
    def put_zone(zone_name: str, body: ZoneIn, request: Request) -> ZoneOut | JSONResponse:
        if error := _query_error(request):
            return error
        if zone_name not in {"food_bowl", "pet_bed"}:
            return _api_error(404, "zone_not_found", "Zone was not found")
        session: Session | None = None
        try:
            session = _session(request.app)
            rows = session.execute(select(Zone).with_for_update()).scalars().all()
            by_name = {row.zone_name: row for row in rows}
            row = by_name.get(zone_name)
            if row is None or set(by_name) != {"food_bowl", "pet_bed"}:
                session.rollback()
                return _api_error(404, "zone_not_found", "Zone was not found")
            row.x1, row.y1, row.x2, row.y2, row.enabled = body.x1, body.y1, body.x2, body.y2, body.enabled
            row.updated_at = request.app.state.clock.utc_now()
            if _overlap(by_name["food_bowl"], by_name["pet_bed"]):
                session.rollback()
                return _api_error(409, "zone_conflict", "Enabled zones must not overlap")
            session.commit()
            request.app.state.camera_service.replace_zones(
                {
                    name: (zone.x1, zone.y1, zone.x2, zone.y2)
                    for name, zone in by_name.items()
                    if zone.enabled
                }
            )
            return ZoneOut(
                zone_name=row.zone_name,
                x1=row.x1,
                y1=row.y1,
                x2=row.x2,
                y2=row.y2,
                enabled=row.enabled,
                updated_at=_aware(row.updated_at),
            )
        except (SQLAlchemyError, DatabaseUnavailableError):
            if session is not None:
                session.rollback()
            return _api_error(503, "database_unavailable", "Database is unavailable")
        finally:
            if session is not None:
                session.close()

    @router.websocket("/ws/dashboard")
    async def dashboard_websocket(websocket: WebSocket) -> None:
        if websocket.headers.get("origin") not in origins or websocket.query_params:
            await websocket.close(code=1008)
            return
        hub = websocket.app.state.dashboard_hub
        await websocket.accept()
        hub.subscribe(websocket)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(websocket)

    application.include_router(router)
