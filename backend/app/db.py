import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, LargeBinary, String, UniqueConstraint, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

load_dotenv()

database_url = os.getenv("SUPABASE_DATABASE_URL") or os.getenv("DATABASE_URL", "sqlite:///./healthcore.sqlite3")
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    **({"pool_pre_ping": True, "pool_size": int(os.getenv("DB_POOL_SIZE", "2")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "0")), "pool_recycle": 300,
        "pool_timeout": 10}
       if database_url.startswith("postgresql") else {}),
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


class Turbine(Base):
    __tablename__ = "turbines"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    maps_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ModelRevision(Base):
    __tablename__ = "model_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    artifact_path: Mapped[str] = mapped_column(String(500))
    training_rows: Mapped[int] = mapped_column(Integer)
    validation_rows: Mapped[int] = mapped_column(Integer)
    validation_mae: Mapped[float] = mapped_column(Float)
    training_max_at: Mapped[datetime] = mapped_column(DateTime)
    turbine_ids: Mapped[list] = mapped_column(JSON)


class ModelArtifact(Base):
    __tablename__ = "model_artifacts"

    revision_id: Mapped[int] = mapped_column(ForeignKey("model_revisions.id"), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)


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
            for table_name in ("measurements", "forecast_runs", "forecast_points", "turbines", "model_revisions", "model_artifacts"):
                rls_enabled = connection.scalar(
                    text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:table_name)"),
                    {"table_name": f"public.{table_name}"},
                )
                if not rls_enabled:
                    connection.execute(text(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY'))
        for turbine_id, name, latitude, longitude, maps_url in (
            (1, "Турбина 01", 43.645150, 78.535604, "https://maps.app.goo.gl/iN6svMt69D5qRpFU9"),
            (2, "Турбина 02", 43.643198, 78.538828, "https://maps.app.goo.gl/8UQMwsYavY6nLvFY8"),
        ):
            if connection.scalar(text("SELECT id FROM turbines WHERE id=:id"), {"id": turbine_id}) is None:
                connection.execute(Turbine.__table__.insert().values(id=turbine_id, name=name,
                    latitude=latitude, longitude=longitude, maps_url=maps_url))
        if bind.dialect.name == "postgresql":
            connection.execute(text("SELECT setval(pg_get_serial_sequence('turbines', 'id'), "
                                    "GREATEST((SELECT max(id) FROM turbines), 1))"))
        connection.execute(text("SELECT 1"))
