from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .camera_service import CameraService
from .config import load_config
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
    camera_service = CameraService.disabled()
    worker: RuleWorker | None = None
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
        engine = RuleEngine(config=config, camera_service=camera_service)
        worker = RuleWorker(
            ingress=ingress,
            clock=clock,
            session_factory=session_factory,
            engine=engine,
        )
        worker.start()
        ingestor.start()
        if camera_service.pipeline is not None:
            camera_service.start()
        application.state.rule_ingress = ingress
        application.state.rule_worker = worker
        application.state.mqtt_ingestor = ingestor
        application.state.camera_service = camera_service
        yield
    finally:
        ingress.stop_accepting()
        try:
            ingestor.stop()
        except Exception:
            pass
        try:
            camera_service.shutdown()
        except Exception:
            pass
        if worker is not None:
            worker.shutdown()
        dispose_database()


app = FastAPI(title="PetCare", lifespan=lifespan)
