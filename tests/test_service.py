from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.storage import WeatherStorage
from app.weather import WeatherUpdater, select_local_day, with_local_time


def settings(tmp_path: Path) -> Settings:
    return Settings(
        api_token="0123456789abcdef",
        met_user_agent="weather-test/1.0 test@example.com",
        database_path=tmp_path / "weather.db",
    )


def hourly_item(hour: int) -> dict:
    return {
        "time": f"2026-10-09T{hour:02d}:00:00Z",
        "data": {"instant": {"details": {"air_temperature": 10 + hour}}},
    }


def test_storage_keeps_only_requested_day(tmp_path: Path) -> None:
    storage = WeatherStorage(tmp_path / "weather.db")
    storage.initialize()
    local_date = datetime(2026, 10, 9).date()
    storage.replace_day(local_date, [hourly_item(0), hourly_item(1)], {"units": {}})

    assert len(storage.get_day(local_date)) == 2
    now = datetime(2026, 10, 9, 3, 15, tzinfo=ZoneInfo("Europe/Moscow"))
    assert storage.get_current(now)["time"] == "2026-10-09T00:00:00Z"
    storage.close()


def test_updater_filters_to_local_today(monkeypatch, tmp_path: Path) -> None:
    configured = settings(tmp_path)
    storage = WeatherStorage(configured.database_path)
    storage.initialize()
    now = datetime.now(timezone.utc)
    payload = {
        "properties": {
            "meta": {"updated_at": now.isoformat(), "units": {"air_temperature": "celsius"}},
            "timeseries": [
                {"time": now.replace(minute=0, second=0, microsecond=0).isoformat(), "data": {}},
                {"time": "2000-01-01T00:00:00+00:00", "data": {}},
            ],
        }
    }

    async def fake_get(self, url, *, params, headers):
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert asyncio.run(WeatherUpdater(configured, storage).refresh()) == 1
    assert len(storage.get_day(datetime.now(ZoneInfo(configured.timezone)).date())) == 1
    storage.close()


def test_moscow_day_boundaries() -> None:
    moscow = ZoneInfo("Europe/Moscow")
    items = [
        {
            "time": "2026-10-08T20:00:00Z",  # 23:00 on October 8 in Moscow
            "data": {"instant": {"details": {"air_temperature": -10, "wind_speed": 99}}},
        },
        {
            "time": "2026-10-08T21:00:00Z",  # 00:00 on October 9 in Moscow
            "data": {"instant": {"details": {"air_temperature": 4, "wind_speed": 2}}},
        },
        {
            "time": "2026-10-09T20:00:00Z",  # 23:00 on October 9 in Moscow
            "data": {"instant": {"details": {"air_temperature": 12, "wind_speed": 7}}},
        },
        {
            "time": "2026-10-09T21:00:00Z",  # 00:00 on October 10 in Moscow
            "data": {"instant": {"details": {"air_temperature": 50, "wind_speed": 50}}},
        },
    ]

    selected = select_local_day(items, datetime(2026, 10, 9).date(), moscow)
    assert [item["time"] for item in selected] == [
        "2026-10-08T21:00:00Z",
        "2026-10-09T20:00:00Z",
    ]


def test_mobile_forecast_has_only_local_time_and_mmhg_pressure() -> None:
    cached = {
        "time": "2026-10-09T11:00:00Z",
        "data": {
            "instant": {
                "details": {
                    "air_temperature": 10.5,
                    "air_pressure_at_sea_level": 1000.0,
                }
            }
        },
    }

    mobile = with_local_time(cached, ZoneInfo("Europe/Moscow"))

    assert "time" not in mobile
    assert mobile["local_time"] == "2026-10-09T14:00:00+03:00"
    assert mobile["data"]["instant"]["details"]["air_pressure_at_sea_level"] == 750.1
    # The cached upstream value must remain untouched.
    assert cached["data"]["instant"]["details"]["air_pressure_at_sea_level"] == 1000.0


def test_api_requires_token_and_reads_cache(monkeypatch, tmp_path: Path) -> None:
    configured = settings(tmp_path)

    async def no_refresh(self):
        return 0

    monkeypatch.setattr("app.main.get_settings", lambda: configured)
    monkeypatch.setattr(WeatherUpdater, "refresh", no_refresh)
    with TestClient(app, raise_server_exceptions=True) as client:
        today = datetime.now(ZoneInfo(configured.timezone)).date()
        current_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        item = {"time": current_utc.isoformat(), "data": {"instant": {"details": {}}}}
        app.state.storage.replace_day(
            today, [item], {"units": {}, "fetched_at": "test"}
        )
        assert client.get("/api/v1/forecast/day").status_code == 401
        response = client.get(
            "/api/v1/forecast/day",
            headers={"Authorization": f"Bearer {configured.api_token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["forecasts"]) == 1
        assert payload["forecasts"][0]["local_time"].endswith("+03:00")
        assert "time" not in payload["forecasts"][0]
        assert "source" not in payload
        assert "location" not in payload
        assert "date" not in payload
        assert "daily_extremes" not in payload
