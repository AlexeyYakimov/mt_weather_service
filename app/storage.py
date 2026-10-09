from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.timeutils import parse_iso_datetime


class WeatherStorage:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS forecasts (
                    forecast_time TEXT PRIMARY KEY,
                    local_date TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS forecasts_local_date_idx
                    ON forecasts(local_date, forecast_time);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def replace_day(
        self,
        local_date: date,
        forecasts: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        date_text = local_date.isoformat()
        with self._lock, self._connection:
            # Keeping only today's data is part of the same atomic transaction as refresh.
            self._connection.execute("DELETE FROM forecasts WHERE local_date != ?", (date_text,))
            self._connection.execute("DELETE FROM forecasts WHERE local_date = ?", (date_text,))
            self._connection.executemany(
                "INSERT INTO forecasts(forecast_time, local_date, payload) VALUES (?, ?, ?)",
                [
                    (item["time"], date_text, json.dumps(item, separators=(",", ":")))
                    for item in forecasts
                ],
            )
            self._connection.executemany(
                """
                INSERT INTO metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                [(key, json.dumps(value)) for key, value in metadata.items()],
            )

    def prune_except(self, local_date: date) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM forecasts WHERE local_date != ?", (local_date.isoformat(),)
            )

    def get_day(self, local_date: date) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM forecasts WHERE local_date = ? ORDER BY forecast_time",
                (local_date.isoformat(),),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_metadata(self) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def get_current(self, now: datetime) -> dict[str, Any] | None:
        items = self.get_day(now.date())
        if not items:
            return None

        target = now.replace(minute=0, second=0, microsecond=0)
        exact = next(
            (
                item
                for item in items
                if parse_iso_datetime(item["time"]).astimezone(now.tzinfo) == target
            ),
            None,
        )
        return exact

    def close(self) -> None:
        with self._lock:
            self._connection.close()
