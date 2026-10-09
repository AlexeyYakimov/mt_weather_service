from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    api_token: str = Field(min_length=16)
    latitude: float = Field(default=55.60, ge=-90, le=90)
    longitude: float = Field(default=37.41, ge=-180, le=180)
    timezone: str = "Europe/Moscow"
    update_interval_hours: float = Field(default=2, gt=0)
    database_path: Path = Path("/data/weather.db")
    met_api_url: str = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
    met_user_agent: str = Field(min_length=8)
    request_timeout_seconds: float = Field(default=30, gt=0)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
