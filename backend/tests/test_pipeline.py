import io
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.db import Measurement, ModelArtifact, ModelRevision, Turbine, init_db
from backend.app.pipeline import SOURCE_COLUMNS, create_forecast, import_csv_stream, train_model
from backend.app.turbines import coordinates_from_maps_url
from backend.app.weather import fetch_weather, issue_and_weather_run


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
        for artifact in Path(self.temp.name).glob("*.joblib"):
            artifact.unlink()
        issued, _ = issue_and_weather_run(date(2026, 1, 31))
        weather = {issued.astimezone(timezone.utc) + timedelta(hours=i): (8.0, 5.0)
                   for i in range(1, 49)}
        with patch("backend.app.pipeline.fetch_weather", return_value=weather):
            run = create_forecast(self.session, date(2026, 1, 31), [1], second["revision_id"])
        self.assertEqual(len(run.points), 48)
        self.assertEqual({point.turbine_id for point in run.points}, {1})
        self.assertEqual(run.model_version, second["model_version"])
        with self.assertRaisesRegex(ValueError, "unavailable at forecast issue time"):
            create_forecast(self.session, date(2026, 1, 10), [1], second["revision_id"])
        self.session.add(Turbine(id=3, name="Late turbine", latitude=43.7, longitude=78.6))
        self.session.add_all(Measurement(turbine_id=3, source_id=i + 1,
                                         observed_at=datetime(2026, 1, 25) + timedelta(hours=i),
                                         wind_speed_ms=7, normalized_power=.5, temperature_c=5,
                                         original={}) for i in range(10))
        self.session.commit()
        with patch("backend.app.pipeline.MODEL_DIR", Path(self.temp.name)):
            third = train_model(self.session)
        self.assertEqual(third["turbine_ids"], [1], "A turbine only in holdout must not be forecastable")

    def test_weather_run_precedes_decision(self):
        issued, weather_run = issue_and_weather_run(date(2026, 2, 1))
        self.assertLess(weather_run, issued)
        self.assertEqual(weather_run.strftime("%Y-%m-%d %H:%M"), "2026-01-31 12:00")

    def test_archived_weather_uses_selected_coordinates_and_run(self):
        data = {"hourly_units": {"wind_speed_100m": "m/s", "temperature_2m": "°C"},
                "hourly": {"time": ["2026-02-01T00:00"], "wind_speed_100m": [7.5],
                           "temperature_2m": [-2.0]}}
        with patch("backend.app.weather.httpx.Client") as client:
            response = client.return_value.__enter__.return_value.get.return_value
            response.json.return_value = data
            run_at = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
            result = fetch_weather(43.645150, 78.535604, run_at)
            url, kwargs = client.return_value.__enter__.return_value.get.call_args.args[0], client.return_value.__enter__.return_value.get.call_args.kwargs
        self.assertIn("single-runs-api", url)
        self.assertEqual(kwargs["params"]["run"], "2026-01-31T12:00")
        self.assertEqual(kwargs["params"]["latitude"], 43.645150)
        self.assertEqual(kwargs["params"]["longitude"], 78.535604)
        self.assertEqual(kwargs["params"]["models"], "ecmwf_ifs")
        self.assertEqual(result[datetime(2026, 2, 1, tzinfo=timezone.utc)], (7.5, -2.0))

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
