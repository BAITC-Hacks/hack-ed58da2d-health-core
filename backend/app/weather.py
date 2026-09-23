from datetime import date, datetime, timedelta, timezone
import math
import os

import httpx

KAZAKHSTAN_OFFSET = timezone(timedelta(hours=5))


def issue_and_weather_run(issue_date: date) -> tuple[datetime, datetime]:
    # Daily decision at 00:00 Kazakhstan time; the previous 12 UTC ECMWF run
    # has a conservative 7-hour publication margin before this decision.
    issued_at = datetime(issue_date.year, issue_date.month, issue_date.day, tzinfo=KAZAKHSTAN_OFFSET)
    run_at = issued_at.astimezone(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    return issued_at, run_at


def fetch_weather(latitude: float, longitude: float, run_at: datetime) -> dict[datetime, tuple[float, float]]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "run": run_at.strftime("%Y-%m-%dT%H:%M"),
        "models": "ecmwf_ifs",
        "hourly": "wind_speed_100m,temperature_2m",
        "wind_speed_unit": "ms",
        "timezone": "UTC",
        "forecast_days": 4,
    }
    url = os.getenv("OPEN_METEO_BASE_URL", "https://single-runs-api.open-meteo.com/v1/forecast")
    with httpx.Client(timeout=30) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    if data.get("error"):
        raise ValueError(data.get("reason", "Weather provider error"))
    hourly = data["hourly"]
    units = data.get("hourly_units", {})
    if units and (units.get("wind_speed_100m") not in ("m/s", "ms") or
                  units.get("temperature_2m") != "°C"):
        raise ValueError("Unexpected weather units")
    result = {}
    for stamp, wind, temp in zip(hourly["time"], hourly["wind_speed_100m"],
                                 hourly["temperature_2m"], strict=True):
        if wind is None or temp is None:
            continue
        valid_at = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
        wind, temp = float(wind), float(temp)
        if not math.isfinite(wind) or not math.isfinite(temp) or wind < 0 or valid_at in result:
            raise ValueError("Invalid archived weather values")
        result[valid_at] = (wind, temp)
    return result
