import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from backend.app.db import init_db


class DatabaseBootstrapTests(unittest.TestCase):
    def test_bootstrap_is_idempotent_and_keeps_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.sqlite3"
            engine = create_engine(f"sqlite:///{database.as_posix()}")
            init_db(engine)
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO measurements "
                                        "(turbine_id, source_id, observed_at, wind_speed_ms, "
                                        "normalized_power, temperature_c, original) "
                                        "VALUES (1, 1, '2026-01-31 00:00:00', 5, 0.2, 10, '{}')"))

            init_db(engine)

            inspector = inspect(engine)
            self.assertEqual(set(inspector.get_table_names()),
                             {"measurements", "forecast_runs", "forecast_points", "turbines", "model_revisions", "model_artifacts"})
            self.assertIn("ix_measurements_turbine_observed",
                          {item["name"] for item in inspector.get_indexes("measurements")})
            with engine.connect() as connection:
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM measurements")), 1)
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM turbines")), 2)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
