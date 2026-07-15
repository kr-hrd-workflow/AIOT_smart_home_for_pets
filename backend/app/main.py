from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import load_config
from .db import configure_database, dispose_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    config = load_config()
    configure_database(config.database_url)
    try:
        yield
    finally:
        dispose_database()


app = FastAPI(title="PetCare", lifespan=lifespan)
