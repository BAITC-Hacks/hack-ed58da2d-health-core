import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .db import ForecastPoint, ForecastRun, Measurement
from .weather import COORDINATES, KAZAKHSTAN_OFFSET, fetch_weather, issue_and_weather_run

SOURCE_COLUMNS = (
    "ID",
    "Статистическое время",
    "Средняя скорость ветра(m/s)",
    "Нормализованная активная мощность",
    "Средняя температура окружающей среды(°C)",
)
MODEL_PATH = Path(os.getenv("MODEL_PATH", "./model.joblib"))


def import_csv(session: Session, path: Path, turbine_id: int) -> int:
    if turbine_id not in COORDINATES:
        raise ValueError("Unknown turbine")
    dialect_insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    imported = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != SOURCE_COLUMNS:
            raise ValueError("Unexpected CSV columns")
        batch = []
        for row in reader:
            batch.append({
                "turbine_id": turbine_id,
                "source_id": int(row[SOURCE_COLUMNS[0]]),
                "observed_at": datetime.fromisoformat(row[SOURCE_COLUMNS[1]]),
                "wind_speed_ms": float(row[SOURCE_COLUMNS[2]]),
                "normalized_power": float(row[SOURCE_COLUMNS[3]]),
                "temperature_c": float(row[SOURCE_COLUMNS[4]]),
                "original": row,
            })
            if len(batch) == 1000:
                statement = dialect_insert(Measurement).values(batch).on_conflict_do_nothing(
                    index_elements=["turbine_id", "source_id"]
                )
                imported += session.execute(statement).rowcount
                session.commit()
                batch = []
        if batch:
            statement = dialect_insert(Measurement).values(batch).on_conflict_do_nothing(
                index_elements=["turbine_id", "source_id"]
            )
            imported += session.execute(statement).rowcount
            session.commit()
    return imported


def features(frame: pd.DataFrame) -> np.ndarray:
    hour = frame["observed_at"].dt.hour.to_numpy()
    day = frame["observed_at"].dt.dayofyear.to_numpy()
    return np.column_stack((
        frame["wind_speed_ms"].to_numpy(),
        frame["temperature_c"].to_numpy(),
        np.sin(2 * np.pi * hour / 24),
        np.cos(2 * np.pi * hour / 24),
        np.sin(2 * np.pi * day / 365.25),
        np.cos(2 * np.pi * day / 365.25),
        frame["turbine_id"].to_numpy(),
    ))


def train_model(session: Session) -> dict:
    rows = session.execute(select(
        Measurement.turbine_id, Measurement.observed_at,
        Measurement.wind_speed_ms, Measurement.temperature_c, Measurement.normalized_power
    )).all()
    if len(rows) < 1000:
        raise ValueError("Import at least 1000 measurements before training")
    frame = pd.DataFrame(rows, columns=["turbine_id", "observed_at", "wind_speed_ms", "temperature_c", "normalized_power"])
    frame["observed_at"] = pd.to_datetime(frame["observed_at"])
    frame = (frame.set_index("observed_at").groupby("turbine_id")
             [["wind_speed_ms", "temperature_c", "normalized_power"]]
             .resample("h").mean().dropna().reset_index())
    frame = frame.sort_values("observed_at")
    split_time = frame["observed_at"].quantile(0.9)
    train, validation = frame[frame["observed_at"] < split_time], frame[frame["observed_at"] >= split_time]
    model = HistGradientBoostingRegressor(max_iter=100, max_leaf_nodes=20, l2_regularization=0.1, random_state=42)
    model.fit(features(train), train["normalized_power"])
    prediction = np.clip(model.predict(features(validation)), 0, 1)
    metric = float(mean_absolute_error(validation["normalized_power"], prediction))
    version = hashlib.sha256(f"{len(rows)}:{frame['observed_at'].max()}".encode()).hexdigest()[:12]
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "version": version, "validation_mae": metric}, MODEL_PATH)
    return {"model_version": version, "hourly_train_rows": len(train), "hourly_validation_rows": len(validation), "validation_mae": metric,
            "warning": "Validation uses measured turbine weather; forecast-weather calibration remains to be measured."}


def create_forecast(session: Session, issue_date: date) -> ForecastRun:
    artifact = joblib.load(MODEL_PATH)
    issued_at, weather_run_at = issue_and_weather_run(issue_date)
    timeline = [issued_at.astimezone(timezone.utc) + timedelta(hours=offset) for offset in range(1, 49)]
    rows = []
    for turbine_id in COORDINATES:
        weather = fetch_weather(turbine_id, weather_run_at)
        for valid_at in timeline:
            if valid_at not in weather:
                raise ValueError(f"Missing weather for {valid_at.isoformat()}")
            wind, temperature = weather[valid_at]
            rows.append({"turbine_id": turbine_id,
                         "observed_at": valid_at.astimezone(KAZAKHSTAN_OFFSET).replace(tzinfo=None),
                         "valid_at": valid_at.replace(tzinfo=None),
                         "wind_speed_ms": wind, "temperature_c": temperature})
    frame = pd.DataFrame(rows)
    frame["observed_at"] = pd.to_datetime(frame["observed_at"])
    predictions = np.clip(artifact["model"].predict(features(frame)), 0, 1)
    run = ForecastRun(issued_at=issued_at.replace(tzinfo=None), weather_run_at=weather_run_at.replace(tzinfo=None),
                      model_version=artifact["version"], status="complete",
                      analysis={"points": len(rows), "min": float(predictions.min()), "max": float(predictions.max()),
                                "mean": float(predictions.mean()), "source": "Open-Meteo ECMWF IFS single run"})
    run.points = [ForecastPoint(turbine_id=int(row["turbine_id"]), valid_at=row["valid_at"],
                                wind_speed_ms=float(row["wind_speed_ms"]), temperature_c=float(row["temperature_c"]),
                                normalized_power=float(prediction)) for row, prediction in zip(rows, predictions, strict=True)]
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
