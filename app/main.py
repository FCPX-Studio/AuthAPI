from contextlib import asynccontextmanager, suppress
from fastapi import APIRouter, FastAPI

from db import close_db
from events import EventConsumer, close_redis
from events.streams import ORDERS_READY

from config import settings

from app.events import OrderReadyHandler
from app.routers import license
from app.services import Licensing

import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup logic ---
    app.state.licensing = Licensing()

    # orders:ready consumer (PaddleSync -> AuthAPI), runs alongside the HTTP endpoints
    consumer = EventConsumer(ORDERS_READY, group="authapi")
    consumer_task = asyncio.create_task(consumer.run(OrderReadyHandler(app.state.licensing)))

    yield

    # --- shutdown logic ---
    # A message interrupted by the cancel stays pending in Redis and is retried
    consumer_task.cancel()
    with suppress(asyncio.CancelledError):
        await consumer_task
    await close_redis()
    del app.state.licensing
    close_db()


api_prefix = f"/{settings.API_NAME}/{settings.API_VERSION}"

app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url=f"{api_prefix}/docs",
    redoc_url=f"{api_prefix}/redoc",
    swagger_ui_oauth2_redirect_url=f"{api_prefix}/docs/oauth2-redirect",
    openapi_url=f"{api_prefix}/openapi.json",
)


# Include routers
api = APIRouter(prefix=api_prefix)
api.include_router(license.router)
app.include_router(api)
