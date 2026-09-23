import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select

from backend.app.db import ForecastPoint, ForecastRun, Measurement, init_db
from backend.migrate_sqlite_to_postgres import migrate


class MigrationTests(unittest.TestCase):
    def test_complete_copy_preserves_ids_json_and_relations(self):
        with tempfile.TemporaryDirectory() as directory:
            source = create_engine(f"sqlite:///{(Path(directory) / 'source.db').as_posix()}")
            destination = create_engine(f"sqlite:///{(Path(directory) / 'target.db').as_posix()}")
            init_db(source)
            with source.begin() as connection:
                connection.execute(Measurement.__table__.insert(), {
                    "id": 12, "turbine_id": 2, "source_id": 7,
                    "observed_at": datetime(2026, 1, 31), "wind_speed_ms": 5.0,
                    "normalized_power": 0.4, "temperature_c": -1.0,
                    "original": {"source_id": 7, "raw": "preserved"},
                })
                connection.execute(ForecastRun.__table__.insert(), {
                    "id": 4, "issued_at": datetime(2026, 1, 31),
                    "weather_run_at": datetime(2026, 1, 30),
                    "model_version": "v1", "status": "complete", "analysis": {"ok": True},
                })
                connection.execute(ForecastPoint.__table__.insert(), {
                    "id": 19, "run_id": 4, "turbine_id": 2,
                    "valid_at": datetime(2026, 1, 31, 1), "wind_speed_ms": 6.0,
                    "temperature_c": -2.0, "normalized_power": 0.5,
                })

            self.assertEqual(migrate(source, destination, batch_size=1),
                             {"measurements": 1, "forecast_runs": 1, "forecast_points": 1})
            with destination.connect() as connection:
                row = connection.execute(select(Measurement.__table__)).mappings().one()
                point = connection.execute(select(ForecastPoint.__table__)).mappings().one()
                self.assertEqual(row["id"], 12)
                self.assertEqual(row["original"], {"source_id": 7, "raw": "preserved"})
                self.assertEqual(point["run_id"], 4)

            with self.assertRaisesRegex(ValueError, "not empty"):
                migrate(source, destination, batch_size=1)
            with destination.connect() as connection:
                self.assertEqual(connection.scalar(select(func.count()).select_from(Measurement)), 1)
            source.dispose()
            destination.dispose()
