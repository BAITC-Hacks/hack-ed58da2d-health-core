import csv
from datetime import date, datetime, timedelta, timezone
import io
import math
import os
from pathlib import Path
from uuid import uuid4

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .db import ForecastPoint, ForecastRun, Measurement, ModelArtifact, ModelRevision, Turbine
from .weather import KAZAKHSTAN_OFFSET, fetch_weather, issue_and_weather_run

SOURCE_COLUMNS = (
    "ID",
    "Статистическое время",
    "Средняя скорость ветра(m/s)",
    "Нормализованная активная мощность",
    "Средняя температура окружающей среды(°C)",
)
TRAINING_CUTOFF = datetime(2026, 2, 1)
MODEL_PATH = Path(os.getenv("MODEL_PATH", "./model.joblib"))
MODEL_DIR = Path(os.getenv("MODEL_DIR", "./models"))


def _parse_csv(handle):
    reader = csv.DictReader(handle)
    if tuple(reader.fieldnames or ()) != SOURCE_COLUMNS:
        raise ValueError("Unexpected CSV columns")
    for line, row in enumerate(reader, start=2):
        try:
            if None in row or any(value is None or not value.strip() for value in row.values()):
                raise ValueError("Missing or extra CSV field")
            source_id = int(row[SOURCE_COLUMNS[0]])
            observed_at = datetime.strptime(row[SOURCE_COLUMNS[1]], "%Y-%m-%d %H:%M:%S")
            wind = float(row[SOURCE_COLUMNS[2]])
            power = float(row[SOURCE_COLUMNS[3]])
            temperature = float(row[SOURCE_COLUMNS[4]])
            if source_id < 1 or not all(map(math.isfinite, (wind, power, temperature))) or wind < 0 or not 0 <= power <= 1:
                raise ValueError("Out-of-range or non-finite value")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid CSV row {line}: {exc}") from exc
        yield {"source_id": source_id, "observed_at": observed_at, "wind_speed_ms": wind,
               "normalized_power": power, "temperature_c": temperature, "original": row}


def import_csv_stream(session: Session, binary_handle, turbine_id: int) -> dict:
    if session.get(Turbine, turbine_id) is None:
        raise ValueError("Unknown turbine")
    dialect_insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    imported = 0
    total = 0
    # Preflight the entire file before writing; malformed rows never produce a partial import.
    try:
        handle = io.TextIOWrapper(binary_handle, encoding="utf-8-sig", newline="")
        try:
            for _ in _parse_csv(handle):
                total += 1
        finally:
            handle.detach()
        binary_handle.seek(0)
        handle = io.TextIOWrapper(binary_handle, encoding="utf-8-sig", newline="")
        try:
            batch = []
            for row in _parse_csv(handle):
                batch.append({"turbine_id": turbine_id, **row})
                if len(batch) == 1000:
                    statement = dialect_insert(Measurement).values(batch).on_conflict_do_nothing(
                        index_elements=["turbine_id", "source_id"])
                    imported += session.execute(statement).rowcount
                    batch = []
            if batch:
                statement = dialect_insert(Measurement).values(batch).on_conflict_do_nothing(
                    index_elements=["turbine_id", "source_id"])
                imported += session.execute(statement).rowcount
            session.commit()
        finally:
            handle.detach()
    except (UnicodeError, csv.Error) as exc:
        session.rollback()
        raise ValueError(f"Invalid CSV encoding or structure: {exc}") from exc
    except Exception:
        session.rollback()
        raise
    return {"rows": total, "imported": imported, "skipped": total - imported}


def import_csv(session: Session, path: Path, turbine_id: int) -> int:
    with path.open("rb") as handle:
        return import_csv_stream(session, handle, turbine_id)["imported"]


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
    ).where(Measurement.observed_at < TRAINING_CUTOFF)).all()
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
    version = uuid4().hex[:12]
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = MODEL_DIR / f"model-{version}.joblib"
    turbine_ids = sorted(int(item) for item in frame["turbine_id"].unique())
    joblib.dump({"model": model, "version": version, "validation_mae": metric,
                "turbine_ids": turbine_ids}, artifact_path)
    revision = ModelRevision(version=version, artifact_path=str(artifact_path), training_rows=len(train),
                             validation_rows=len(validation), validation_mae=metric,
                             training_max_at=frame["observed_at"].max().to_pydatetime(), turbine_ids=turbine_ids)
    try:
        session.add(revision)
        session.flush()
        session.add(ModelArtifact(revision_id=revision.id, data=artifact_path.read_bytes()))
        session.commit()
        session.refresh(revision)
    except Exception:
        session.rollback()
        artifact_path.unlink(missing_ok=True)
        raise
    return {"revision_id": revision.id, "model_version": version, "hourly_train_rows": len(train),
            "hourly_validation_rows": len(validation), "validation_mae": metric,
            "training_max_at": revision.training_max_at, "turbine_ids": turbine_ids,
            "warning": "Validation uses measured turbine weather; forecast-weather calibration remains to be measured."}


def create_forecast(session: Session, issue_date: date, turbine_ids: list[int] | None = None,
                    revision_id: int | None = None) -> ForecastRun:
    revision = (session.get(ModelRevision, revision_id) if revision_id is not None else
                session.scalar(select(ModelRevision).order_by(ModelRevision.id.desc())))
    if revision_id is not None and revision is None:
        raise ValueError("Model revision not found")
    if revision is None:
        artifact = joblib.load(MODEL_PATH)
    else:
        binary = session.get(ModelArtifact, revision.id)
        if binary is not None:
            artifact = joblib.load(io.BytesIO(binary.data))
        else:
            # Older revisions created before model_artifacts was introduced.
            artifact = joblib.load(revision.artifact_path)
    if turbine_ids is None:
        turbine_ids = list(artifact.get("turbine_ids", [1, 2]))
    if not turbine_ids or len(turbine_ids) != len(set(turbine_ids)):
        raise ValueError("Select one or more unique turbines")
    unsupported = set(turbine_ids) - set(artifact.get("turbine_ids", [1, 2]))
    if unsupported:
        raise ValueError(f"Model has no training data for turbines: {sorted(unsupported)}")
    turbines = session.scalars(select(Turbine).where(Turbine.id.in_(turbine_ids))).all()
    if len(turbines) != len(turbine_ids):
        raise ValueError("Unknown turbine")
    issued_at, weather_run_at = issue_and_weather_run(issue_date)
    timeline = [issued_at.astimezone(timezone.utc) + timedelta(hours=offset) for offset in range(1, 49)]
    rows = []
    for turbine in turbines:
        weather = fetch_weather(turbine.latitude, turbine.longitude, weather_run_at)
        for valid_at in timeline:
            if valid_at not in weather:
                raise ValueError(f"Missing weather for {valid_at.isoformat()}")
            wind, temperature = weather[valid_at]
            rows.append({"turbine_id": turbine.id,
                         "observed_at": valid_at.astimezone(KAZAKHSTAN_OFFSET).replace(tzinfo=None),
                         "valid_at": valid_at.replace(tzinfo=None),
                         "wind_speed_ms": wind, "temperature_c": temperature})
    frame = pd.DataFrame(rows)
    frame["observed_at"] = pd.to_datetime(frame["observed_at"])
    predictions = np.clip(artifact["model"].predict(features(frame)), 0, 1)
    run = ForecastRun(issued_at=issued_at.replace(tzinfo=None), weather_run_at=weather_run_at.replace(tzinfo=None),
                      model_version=artifact["version"], status="complete",
                      analysis={"points": len(rows), "min": float(predictions.min()), "max": float(predictions.max()),
                                "mean": float(predictions.mean()), "source": "Open-Meteo ECMWF IFS single run",
                                "turbine_ids": turbine_ids, "revision_id": revision.id if revision else None,
                                "coordinates": {str(t.id): [t.latitude, t.longitude] for t in turbines}})
    run.points = [ForecastPoint(turbine_id=int(row["turbine_id"]), valid_at=row["valid_at"],
                                wind_speed_ms=float(row["wind_speed_ms"]), temperature_c=float(row["temperature_c"]),
                                normalized_power=float(prediction)) for row, prediction in zip(rows, predictions, strict=True)]
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
