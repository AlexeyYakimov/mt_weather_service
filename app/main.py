from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Annotated, AsyncIterator, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.storage import WeatherStorage
from app.weather import WeatherUpdater, public_response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def update_loop(updater: WeatherUpdater, interval_seconds: float) -> None:
    while True:
        try:
            await updater.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unable to refresh weather cache; retaining last valid cache")
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    storage = WeatherStorage(settings.database_path)
    storage.initialize()
    app.state.settings = settings
    app.state.storage = storage
    task = asyncio.create_task(
        update_loop(WeatherUpdater(settings, storage), settings.update_interval_hours * 3600)
    )
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        storage.close()


app = FastAPI(
    title="Weather Cache API",
    version="1.0.0",
    description="Token-protected current-day forecast served exclusively from a local cache.",
    lifespan=lifespan,
)

bearer_scheme = HTTPBearer(auto_error=False)


def authorize(
    request: Request,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)
    ] = None,
) -> None:
    valid = (
        credentials is not None
        and credentials.scheme.lower() == "bearer"
        and secrets.compare_digest(
            credentials.credentials, request.app.state.settings.api_token
        )
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def cache_context(request: Request) -> tuple[Settings, WeatherStorage, datetime]:
    settings: Settings = request.app.state.settings
    return settings, request.app.state.storage, datetime.now(ZoneInfo(settings.timezone))


@app.get("/health", tags=["service"])
def health(request: Request) -> dict[str, object]:
    settings, storage, now = cache_context(request)
    items = storage.get_day(now.date())
    return {
        "status": "ok" if items else "degraded",
        "cache_ready": bool(items),
        "cached_hours": len(items),
        "timezone": settings.timezone,
    }


@app.get("/api/v1/forecast/current", dependencies=[Depends(authorize)], tags=["forecast"])
def current_forecast(request: Request) -> dict[str, object]:
    settings, storage, now = cache_context(request)
    forecast = storage.get_current(now)
    if forecast is None:
        raise HTTPException(status_code=503, detail="Current-hour forecast is not cached yet")
    return public_response(settings, storage.get_metadata(), forecast=forecast)


@app.get("/api/v1/forecast/day", dependencies=[Depends(authorize)], tags=["forecast"])
def day_forecast(request: Request) -> dict[str, object]:
    settings, storage, now = cache_context(request)
    forecasts = storage.get_day(now.date())
    if not forecasts:
        raise HTTPException(status_code=503, detail="Today's forecast is not cached yet")
    return public_response(settings, storage.get_metadata(), forecasts=forecasts)
