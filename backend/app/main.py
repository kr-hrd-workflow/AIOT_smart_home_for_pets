from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from .agent_lifecycle import (
    AgentLifecycleComponents,
    build_agent_components,
    start_agent_components,
    stop_agent_components,
)
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
    agent_components: AgentLifecycleComponents | None = None
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

        agent_config_path = os.environ.get("PETCARE_AGENT_CONFIG")
        agent_tools_path = os.environ.get("PETCARE_AGENT_TOOLS")
        if (agent_config_path is None) != (agent_tools_path is None):
            raise ValueError("agent config and tools paths must be provided together")
        if agent_config_path is not None and agent_tools_path is not None:
            config_path = Path(agent_config_path)
            tools_path = Path(agent_tools_path)
            if not config_path.is_absolute() or not tools_path.is_absolute():
                raise ValueError("agent config and tools paths must be absolute")
            agent_components = build_agent_components(config_path, tools_path, session_factory)
            application.state.agent_components = agent_components

        worker.start()
        ingestor.start()
        if (
            getattr(camera_service, "pipeline", None) is not None
            or getattr(camera_service, "jetson_client", None) is not None
        ):
            camera_service.start()
        if agent_components is not None:
            await run_in_threadpool(start_agent_components, agent_components)
        yield
    finally:
        first_error = sys.exception()
        try:
            ingress.stop_accepting()
        except BaseException as error:
            first_error = first_error or error
        try:
            ingestor.stop()
        except BaseException as error:
            first_error = first_error or error
        try:
            if worker is not None:
                await run_in_threadpool(worker.shutdown)
        except BaseException as error:
            first_error = first_error or error
        try:
            if camera_service is not None:
                camera_service.shutdown()
        except BaseException as error:
            first_error = first_error or error
        try:
            if agent_components is not None:
                await run_in_threadpool(stop_agent_components, agent_components)
        except BaseException as error:
            first_error = first_error or error
        try:
            hub.shutdown()
        except BaseException as error:
            first_error = first_error or error
        try:
            await hub_task
        except BaseException as error:
            first_error = first_error or error
        try:
            dispose_database()
        except BaseException as error:
            first_error = first_error or error
        if first_error is not None:
            raise first_error


app = FastAPI(
    title="PetCare",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
install_api(app)
