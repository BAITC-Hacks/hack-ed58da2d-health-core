from datetime import date, datetime, time
import os
import secrets

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .db import ForecastPoint, ForecastRun, Measurement, ModelRevision, SessionLocal, Turbine, init_db
from .pipeline import create_forecast, import_csv_stream, train_model
from .turbines import coordinates_from_maps_url

app = FastAPI(title="Health Core Wind Forecast", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Admin-Key"])


@app.on_event("startup")
def startup() -> None:
    if os.getenv("APP_ENV") == "production" and not os.getenv("API_WRITE_KEY"):
        raise RuntimeError("API_WRITE_KEY is required in production")
    if os.getenv('DB_INIT_ON_STARTUP', 'true').lower() in ('1', 'true', 'yes'):
        init_db()
    else:
        # An existing shared database must not take DDL locks on every API start.
        with SessionLocal() as session:
            session.execute(text('SELECT 1'))


def get_session():
    with SessionLocal() as session:
        yield session


def require_write_key(x_admin_key: str | None = Header(default=None)) -> None:
    """Keep public reads available while protecting all production writes."""
    expected = os.getenv("API_WRITE_KEY")
    if expected and (x_admin_key is None or not secrets.compare_digest(x_admin_key, expected)):
        raise HTTPException(status_code=401, detail="Operator key required for this action")


class MeasurementInput(BaseModel):
    turbine_id: int = Field(ge=1)
    source_id: int = Field(ge=1)
    observed_at: datetime
    wind_speed_ms: float = Field(ge=0)
    normalized_power: float = Field(ge=0, le=1)
    temperature_c: float


class TurbineInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    maps_url: str | None = Field(default=None, max_length=500)


class ForecastInput(BaseModel):
    turbine_ids: list[int] = Field(min_length=1)
    revision_id: int | None = Field(default=None, ge=1)


class TrainInput(BaseModel):
    cutoff_date: date = date(2026, 1, 31)


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ready", "database_backend": session.get_bind().dialect.name}


@app.get("/turbines")
def turbines(session: Session = Depends(get_session)) -> list[dict]:
    return [{"id": t.id, "name": t.name, "latitude": t.latitude, "longitude": t.longitude,
             "maps_url": t.maps_url} for t in session.scalars(select(Turbine).order_by(Turbine.id))]


@app.post("/turbines", status_code=201, dependencies=[Depends(require_write_key)])
def add_turbine(item: TurbineInput, session: Session = Depends(get_session)) -> dict:
    if (item.latitude is None) != (item.longitude is None):
        raise HTTPException(status_code=422, detail="Enter both latitude and longitude")
    if item.latitude is None:
        if not item.maps_url:
            raise HTTPException(status_code=422, detail="Enter coordinates or a Google Maps link")
        try:
            latitude, longitude = coordinates_from_maps_url(item.maps_url)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        latitude, longitude = item.latitude, item.longitude
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise HTTPException(status_code=422, detail="Coordinates are out of range")
    turbine = Turbine(name=item.name.strip(), latitude=latitude, longitude=longitude, maps_url=item.maps_url)
    session.add(turbine)
    session.commit()
    session.refresh(turbine)
    return {"id": turbine.id, "name": turbine.name, "latitude": turbine.latitude,
            "longitude": turbine.longitude, "maps_url": turbine.maps_url}


@app.get("/measurements")
def measurements(turbine_id: int = Query(ge=1), limit: int = Query(default=100, ge=1, le=1000),
                 offset: int = Query(default=0, ge=0), session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(Measurement).where(Measurement.turbine_id == turbine_id)
                           .order_by(Measurement.observed_at).offset(offset).limit(limit)).all()
    return [{"turbine_id": row.turbine_id, "source_id": row.source_id, "observed_at": row.observed_at,
             "original": row.original} for row in rows]


@app.post("/measurements", status_code=201, dependencies=[Depends(require_write_key)])
def add_measurement(item: MeasurementInput, session: Session = Depends(get_session)) -> dict:
    if session.get(Turbine, item.turbine_id) is None:
        raise HTTPException(status_code=422, detail="Unknown turbine")
    existing = session.scalar(select(Measurement).where(Measurement.turbine_id == item.turbine_id,
                                                         Measurement.source_id == item.source_id))
    if existing:
        raise HTTPException(status_code=409, detail="Measurement already exists")
    row = Measurement(**item.model_dump(), original=item.model_dump(mode="json"))
    session.add(row)
    session.commit()
    return {"id": row.id}


@app.post("/measurements/upload", status_code=201, dependencies=[Depends(require_write_key)])
def upload_measurements(turbine_id: int = Query(ge=1), file: UploadFile = File(...),
                        session: Session = Depends(get_session)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Select a CSV file")
    if file.size is not None and file.size > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV exceeds 50 MB")
    try:
        return import_csv_stream(session, file.file, turbine_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/models")
def models(session: Session = Depends(get_session)) -> list[dict]:
    return [{"id": m.id, "version": m.version, "created_at": m.created_at,
             "training_rows": m.training_rows, "validation_rows": m.validation_rows,
             "validation_mae": m.validation_mae, "training_max_at": m.training_max_at,
             "turbine_ids": m.turbine_ids} for m in session.scalars(
                 select(ModelRevision).order_by(ModelRevision.id.desc()))]


@app.post("/models/train", status_code=201, dependencies=[Depends(require_write_key)])
def train(options: TrainInput | None = None, session: Session = Depends(get_session)) -> dict:
    cutoff_date = options.cutoff_date if options else date(2026, 1, 31)
    if cutoff_date > date(2026, 2, 1):
        raise HTTPException(status_code=422, detail="Training cutoff cannot enter the February test period")
    try:
        return train_model(session, datetime.combine(cutoff_date, time.min))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/forecasts/{issue_date}", status_code=201, dependencies=[Depends(require_write_key)])
def forecast(issue_date: date, options: ForecastInput | None = None,
             session: Session = Depends(get_session)) -> dict:
    try:
        run = create_forecast(session, issue_date,
                              options.turbine_ids if options else None,
                              options.revision_id if options else None)
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail="Train the model first") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": run.id, "status": run.status, "analysis": run.analysis}


@app.get("/forecasts")
def list_forecasts(session: Session = Depends(get_session)) -> list[dict]:
    point_count = (select(func.count(ForecastPoint.id))
                   .where(ForecastPoint.run_id == ForecastRun.id)
                   .scalar_subquery())
    runs = session.execute(select(ForecastRun, point_count)
                           .order_by(ForecastRun.issued_at.desc(), ForecastRun.id.desc())
                           .limit(100)).all()
    return [{"id": run.id, "issued_at": run.issued_at, "status": run.status,
             "model_version": run.model_version, "point_count": count,
             "turbine_ids": run.analysis.get("turbine_ids", [])} for run, count in runs]


@app.get("/forecasts/{run_id}")
def get_forecast(run_id: int, session: Session = Depends(get_session)) -> dict:
    run = session.get(ForecastRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Forecast run not found")
    return {"id": run.id, "issued_at": run.issued_at, "weather_run_at": run.weather_run_at,
            "model_version": run.model_version, "status": run.status, "analysis": run.analysis,
            "points": [{"turbine_id": point.turbine_id, "valid_at": point.valid_at,
                        "wind_speed_ms": point.wind_speed_ms, "temperature_c": point.temperature_c,
                        "normalized_power": point.normalized_power} for point in run.points]}
