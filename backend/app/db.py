import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

load_dotenv()

database_url = os.getenv("DATABASE_URL", "sqlite:///./healthcore.sqlite3")
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (UniqueConstraint("turbine_id", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    turbine_id: Mapped[int] = mapped_column(Integer, index=True)
    source_id: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    wind_speed_ms: Mapped[float] = mapped_column(Float)
    normalized_power: Mapped[float] = mapped_column(Float)
    temperature_c: Mapped[float] = mapped_column(Float)
    original: Mapped[dict] = mapped_column(JSON)


class ForecastRun(Base):
    __tablename__ = "forecast_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    weather_run_at: Mapped[datetime] = mapped_column(DateTime)
    model_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    analysis: Mapped[dict] = mapped_column(JSON)
    points: Mapped[list["ForecastPoint"]] = relationship(cascade="all, delete-orphan")


class ForecastPoint(Base):
    __tablename__ = "forecast_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("forecast_runs.id"), index=True)
    turbine_id: Mapped[int] = mapped_column(Integer)
    valid_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    wind_speed_ms: Mapped[float] = mapped_column(Float)
    temperature_c: Mapped[float] = mapped_column(Float)
    normalized_power: Mapped[float] = mapped_column(Float)


def init_db() -> None:
    Base.metadata.create_all(engine)
