import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

load_dotenv()

database_url = os.getenv("DATABASE_URL", "sqlite:///./healthcore.sqlite3")
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    **({"pool_pre_ping": True, "pool_size": 5, "max_overflow": 2} if database_url.startswith("postgresql") else {}),
)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        UniqueConstraint("turbine_id", "source_id"),
        Index("ix_measurements_turbine_observed", "turbine_id", "observed_at"),
    )

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


def init_db(bind: Engine = engine) -> None:
    """Create the initial schema idempotently; block Data API access on PostgreSQL."""
    with bind.begin() as connection:
        Base.metadata.create_all(connection)
        # create_all does not add indexes to tables created by older versions.
        for index in Measurement.__table__.indexes:
            index.create(connection, checkfirst=True)
        if bind.dialect.name == "postgresql":
            # These tables live in Supabase's exposed public schema. No anon or
            # authenticated policies exist: only the backend's DB connection uses them.
            for table_name in ("measurements", "forecast_runs", "forecast_points"):
                connection.execute(text(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY'))
        connection.execute(text("SELECT 1"))
