"""Copy the complete application database to an empty PostgreSQL database."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, func, insert, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from .app.db import ForecastPoint, ForecastRun, Measurement, init_db


TABLES = (Measurement.__table__, ForecastRun.__table__, ForecastPoint.__table__)
ROOT = Path(__file__).resolve().parents[1]


def summary(connection, table) -> tuple[int, int | None, int | None]:
    return connection.execute(select(func.count(), func.max(table.c.id), func.sum(table.c.id))).one()


def migrate(source: Engine, destination: Engine, batch_size: int = 1000) -> dict[str, int]:
    """Copy rows with original IDs in one transaction; never merge into occupied tables."""
    if batch_size < 1 or batch_size > 2000:
        raise ValueError("batch_size must be between 1 and 2000")
    if source.url == destination.url:
        raise ValueError("Source and destination must be different databases")

    # Refuse an occupied destination before bootstrap changes its indexes/RLS.
    with destination.connect() as connection:
        inspector = inspect(connection)
        occupied = [table.name for table in TABLES
                    if inspector.has_table(table.name)
                    and summary(connection, table)[0] != 0]
        if occupied:
            raise ValueError(f"Destination is not empty: {', '.join(occupied)}. No rows were copied.")

    init_db(destination)
    with source.connect() as source_connection, destination.begin() as target_connection:
        expected = {table.name: summary(source_connection, table) for table in TABLES}
        occupied = [table.name for table in TABLES if summary(target_connection, table)[0] != 0]
        if occupied:
            raise ValueError(f"Destination is not empty: {', '.join(occupied)}. No rows were copied.")

        for table in TABLES:
            result = source_connection.execute(select(table).order_by(table.c.id)).mappings()
            copied = 0
            while rows := result.fetchmany(batch_size):
                target_connection.execute(insert(table), [dict(row) for row in rows])
                copied += len(rows)
                if copied % 20000 < batch_size or copied == expected[table.name][0]:
                    print(f"{table.name}: {copied}/{expected[table.name][0]}", flush=True)

            if summary(target_connection, table) != expected[table.name]:
                raise RuntimeError(f"Verification failed for {table.name}; transaction will roll back")

        if destination.dialect.name == "postgresql":
            for table in TABLES:
                sequence = target_connection.scalar(
                    text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                    {"table_name": f"public.{table.name}"},
                )
                if sequence and expected[table.name][1] is not None:
                    target_connection.execute(
                        text("SELECT setval(CAST(:sequence AS regclass), :last_id, true)"),
                        {"sequence": sequence, "last_id": expected[table.name][1]},
                    )

    with destination.connect() as connection:
        for table in TABLES:
            if summary(connection, table) != expected[table.name]:
                raise RuntimeError(f"Post-commit verification failed for {table.name}")
    return {table.name: expected[table.name][0] for table in TABLES}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "healthcore.sqlite3")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    target_url = os.getenv("SUPABASE_DATABASE_URL")
    if not target_url:
        parser.error("Set SUPABASE_DATABASE_URL in the ignored .env file first")
    if make_url(target_url).get_backend_name() != "postgresql":
        parser.error("SUPABASE_DATABASE_URL must point to PostgreSQL")
    if not args.source.is_file():
        parser.error(f"SQLite source not found: {args.source}")

    source = create_engine(f"sqlite:///{args.source.resolve().as_posix()}")
    if target_url.startswith("postgresql://"):
        target_url = target_url.replace("postgresql://", "postgresql+psycopg://", 1)
    destination = create_engine(target_url, pool_pre_ping=True, pool_size=2, max_overflow=0)
    try:
        counts = migrate(source, destination, args.batch_size)
        print(f"Migration verified: {counts}")
    finally:
        source.dispose()
        destination.dispose()


if __name__ == "__main__":
    main()
