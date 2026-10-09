from datetime import datetime


def parse_iso_datetime(value: str) -> datetime:
    """Parse ISO-8601 timestamps, including MET's trailing UTC marker (Z)."""
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    return datetime.fromisoformat(value)
