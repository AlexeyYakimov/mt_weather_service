from __future__ import annotations

from copy import deepcopy
import logging
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.storage import WeatherStorage
from app.timeutils import parse_iso_datetime

logger = logging.getLogger(__name__)

HPA_TO_MMHG = 0.750061683


def select_local_day(
    timeseries: list[dict[str, Any]], local_date: date, timezone_info: ZoneInfo
) -> list[dict[str, Any]]:
    """Select UTC forecast points belonging to a local calendar day."""
    return [
        item
        for item in timeseries
        if parse_iso_datetime(item["time"]).astimezone(timezone_info).date()
        == local_date
    ]


def with_local_time(item: dict[str, Any], timezone_info: ZoneInfo) -> dict[str, Any]:
    data = deepcopy(item.get("data", {}))
    details = data.get("instant", {}).get("details", {})
    pressure = details.get("air_pressure_at_sea_level")
    if isinstance(pressure, (int, float)) and not isinstance(pressure, bool):
        details["air_pressure_at_sea_level"] = round(pressure * HPA_TO_MMHG, 1)

    return {
        "local_time": parse_iso_datetime(item["time"])
        .astimezone(timezone_info)
        .isoformat(),
        "data": data,
    }


class WeatherUpdater:
    def __init__(self, settings: Settings, storage: WeatherStorage) -> None:
        self._settings = settings
        self._storage = storage
        self._timezone = ZoneInfo(settings.timezone)

    async def refresh(self) -> int:
        today = datetime.now(self._timezone).date()
        # At midnight, remove yesterday even if the upstream request subsequently fails.
        self._storage.prune_except(today)
        headers = {
            "User-Agent": self._settings.met_user_agent,
            "Accept": "application/json",
        }
        params = {
            "lat": self._settings.latitude,
            "lon": self._settings.longitude,
        }
        async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
            response = await client.get(
                self._settings.met_api_url, params=params, headers=headers
            )
            response.raise_for_status()
            source = response.json()

        properties = source["properties"]
        forecasts = select_local_day(properties["timeseries"], today, self._timezone)
        if not forecasts:
            raise ValueError("MET response does not contain forecasts for the local current day")

        self._storage.replace_day(
            today,
            forecasts,
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source_updated_at": properties["meta"].get("updated_at"),
                "units": properties["meta"].get("units", {}),
            },
        )
        logger.info("Weather cache refreshed: %d hourly entries for %s", len(forecasts), today)
        return len(forecasts)


def public_response(
    settings: Settings,
    metadata: dict[str, Any],
    *,
    forecast: dict[str, Any] | None = None,
    forecasts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    timezone_info = ZoneInfo(settings.timezone)
    units = dict(metadata.get("units", {}))
    if "air_pressure_at_sea_level" in units:
        units["air_pressure_at_sea_level"] = "mmHg"
    result: dict[str, Any] = {
        "fetched_at": metadata.get("fetched_at"),
        "source_updated_at": metadata.get("source_updated_at"),
        "units": units,
    }
    if forecast is not None:
        result["forecast"] = with_local_time(forecast, timezone_info)
    if forecasts is not None:
        result["forecasts"] = [
            with_local_time(item, timezone_info) for item in forecasts
        ]
    return result
