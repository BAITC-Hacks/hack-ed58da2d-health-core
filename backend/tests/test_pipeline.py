import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.db import Measurement, ModelArtifact, ModelRevision, Turbine, init_db
from backend.app.pipeline import SOURCE_COLUMNS, import_csv_stream, train_model
from backend.app.turbines import coordinates_from_maps_url
from backend.app.weather import issue_and_weather_run


def csv_bytes(*rows):
    return io.BytesIO((",".join(SOURCE_COLUMNS) + "\n" + "\n".join(rows) + "\n").encode("utf-8"))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{(Path(self.temp.name) / 'test.sqlite3').as_posix()}")
        init_db(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_csv_preserves_raw_row_and_skips_duplicate(self):
        source = csv_bytes("1,2023-03-11 0:00:00,6.73,0.39,15.38")
        self.assertEqual(import_csv_stream(self.session, source, 1), {"rows": 1, "imported": 1, "skipped": 0})
        source.seek(0)
        self.assertEqual(import_csv_stream(self.session, source, 1), {"rows": 1, "imported": 0, "skipped": 1})
        row = self.session.scalar(select(Measurement))
        self.assertEqual(row.observed_at, datetime(2023, 3, 11))
        self.assertEqual(row.original[SOURCE_COLUMNS[2]], "6.73")

    def test_invalid_late_row_does_not_partially_import(self):
        source = csv_bytes("1,2023-03-11 0:00:00,6.73,0.39,15.38",
                           "2,2023-03-11 0:10:00,NaN,0.5,15.1")
        with self.assertRaisesRegex(ValueError, "row 3"):
            import_csv_stream(self.session, source, 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Measurement)), 0)

    def test_training_creates_distinct_revisions_and_excludes_february(self):
        start = datetime(2025, 12, 1)
        measurements = [Measurement(turbine_id=1, source_id=i + 1, observed_at=start + timedelta(hours=i),
                                    wind_speed_ms=4 + (i % 12), normalized_power=(i % 12) / 12,
                                    temperature_c=10 + (i % 8), original={}) for i in range(1100)]
        measurements.append(Measurement(turbine_id=1, source_id=1101, observed_at=datetime(2026, 2, 1),
                                        wind_speed_ms=10, normalized_power=1, temperature_c=10, original={}))
        self.session.add_all(measurements)
        self.session.commit()
        with patch("backend.app.pipeline.MODEL_DIR", Path(self.temp.name)):
            first = train_model(self.session)
            second = train_model(self.session)
        self.assertNotEqual(first["model_version"], second["model_version"])
        self.assertEqual(first["hourly_train_rows"] + first["hourly_validation_rows"], 1100)
        self.assertLess(first["training_max_at"], datetime(2026, 2, 1))
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ModelRevision)), 2)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ModelArtifact)), 2)

    def test_weather_run_precedes_decision(self):
        from datetime import date
        issued, weather_run = issue_and_weather_run(date(2026, 2, 1))
        self.assertLess(weather_run, issued)
        self.assertEqual(weather_run.strftime("%Y-%m-%d %H:%M"), "2026-01-31 12:00")

    def test_google_maps_coordinates_and_restricted_hosts(self):
        with patch("backend.app.turbines.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.return_value.status_code = 302
            client.return_value.__enter__.return_value.get.return_value.headers = {
                "location": "https://www.google.com/maps/search/43.645150,+78.535604?entry=tts"}
            self.assertEqual(coordinates_from_maps_url("https://maps.app.goo.gl/example"),
                             (43.645150, 78.535604))
        with self.assertRaisesRegex(ValueError, "Google Maps"):
            coordinates_from_maps_url("https://example.com/maps/@43,78")


if __name__ == "__main__":
    unittest.main()
