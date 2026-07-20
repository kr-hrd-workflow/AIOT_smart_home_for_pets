from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from .api import build_dashboard_summary, install_api
from .camera_service import CameraService, build_camera_service
from .config import load_config
from .contracts import DashboardUpdate
from .dashboard_hub import DashboardHub
from .db import configure_database, dispose_database, session_factory
from .mqtt_ingest import MqttIngestor, load_mqtt_endpoint
from .rule_ingress import RuleIngress, SystemRuleClock
from .rule_worker import RuleWorker
from .rules import RuleEngine


@asynccontextmanager
async def lifespan(application: FastAPI):
    config = load_config()
    configure_database(config.database_url)
    clock = SystemRuleClock()
    ingress = RuleIngress(clock)
    ingestor = MqttIngestor.disabled()
    camera_service: CameraService | None = None
    worker: RuleWorker | None = None
    hub = DashboardHub()
    hub_task = hub.start_broadcaster()
    try:
        if config.mqtt_enabled:
            assert config.mqtt_profile is not None and config.mqtt_username is not None and config.mqtt_password is not None
            ingestor = MqttIngestor(
                ingress=ingress,
                session_factory=session_factory,
                endpoint=load_mqtt_endpoint(config.mqtt_services_manifest, config.mqtt_profile),
                username=config.mqtt_username.get_secret_value(),
                password=config.mqtt_password.get_secret_value(),
            )
        camera_service = build_camera_service(config, ingress, session_factory)
        engine = RuleEngine(config=config, camera_service=camera_service)
        worker = RuleWorker(
            ingress=ingress,
            clock=clock,
            session_factory=session_factory,
            engine=engine,
        )
        application.state.clock = clock
        application.state.session_factory = session_factory
        application.state.rule_ingress = ingress
        application.state.rule_worker = worker
        application.state.mqtt_ingestor = ingestor
        application.state.camera_service = camera_service
        application.state.dashboard_hub = hub
        application.state.rule_engine = engine

        def publish_committed(message: object) -> None:
            hub.publish_from_worker(message)
            try:
                summary = build_dashboard_summary(application)
            except Exception:
                return
            hub.publish_from_worker(DashboardUpdate(type="dashboard_update", payload=summary))

        if hasattr(engine, "publisher"):
            engine.publisher = publish_committed
        if hasattr(engine, "dashboard_publisher"):
            engine.dashboard_publisher = publish_committed
        worker.start()
        ingestor.start()
        if camera_service.pipeline is not None:
            camera_service.start()
        yield
    finally:
        ingress.stop_accepting()
        try:
            ingestor.stop()
        except Exception:
            pass
        try:
            if camera_service is not None:
                camera_service.shutdown()
        except Exception:
            pass
        try:
            if worker is not None:
                await run_in_threadpool(worker.shutdown)
        finally:
            hub.shutdown()
            try:
                await hub_task
            finally:
                dispose_database()


app = FastAPI(
    title="PetCare",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
install_api(app)
