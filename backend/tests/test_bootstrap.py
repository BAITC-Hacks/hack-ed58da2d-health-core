import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.bootstrap import BootstrapManager, train_model
from backend.app.db import Measurement, ModelRevision, init_db
from backend.app.pipeline import SOURCE_COLUMNS


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.engine = create_engine(f"sqlite:///{(self.directory / 'bootstrap.sqlite3').as_posix()}")
        init_db(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _write_csvs(self):
        source_dir = self.directory / "TZ"
        source_dir.mkdir()
        for turbine_id in (1, 2):
            with (source_dir / f"Dataset - turbine {turbine_id}.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(SOURCE_COLUMNS)
                for index in range(1100):
                    timestamp = datetime(2025, 10, 1) + timedelta(hours=index)
                    writer.writerow((index + 1, timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                                     f"{5 + index % 9:.2f}", f"{(index % 9) / 10:.2f}", "10.0"))
        return source_dir

    def test_missing_csv_explains_why_forecast_is_unavailable(self):
        manager = BootstrapManager()
        manager.run(self.sessions, self.directory / "TZ")
        with Session(self.engine) as session:
            state = manager.snapshot(session)
        self.assertEqual(state["status"], "unavailable")
        self.assertFalse(state["can_forecast"])
        self.assertIn("CSV", state["message"])
        self.assertIn("1, 2", state["message"])

    def test_first_start_imports_trains_and_restart_does_not_duplicate(self):
        source_dir = self._write_csvs()
        manager = BootstrapManager()
        with patch("backend.app.pipeline.MODEL_DIR", self.directory / "models"):
            manager.run(self.sessions, source_dir)
            with Session(self.engine) as session:
                state = manager.snapshot(session)
                self.assertEqual(state["status"], "ready")
                self.assertTrue(state["can_forecast"])
                self.assertEqual(state["supported_turbine_ids"], [1, 2])
                self.assertEqual(session.scalar(select(func.count()).select_from(Measurement)), 2200)
            manager.run(self.sessions, source_dir)
        with Session(self.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Measurement)), 2200)
            self.assertEqual(session.scalar(select(func.count()).select_from(ModelRevision)), 1)

    def test_readiness_reports_training_before_model_is_available(self):
        source_dir = self._write_csvs()
        manager = BootstrapManager()
        entered, release = Event(), Event()

        def gated_train(session, cutoff):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("training gate timed out")
            return train_model(session, cutoff)

        with patch("backend.app.pipeline.MODEL_DIR", self.directory / "models"), \
                patch("backend.app.bootstrap.train_model", side_effect=gated_train):
            worker = Thread(target=manager.run, args=(self.sessions, source_dir))
            worker.start()
            try:
                self.assertTrue(entered.wait(10))
                with Session(self.engine) as session:
                    state = manager.snapshot(session)
                self.assertEqual(state["status"], "training")
                self.assertFalse(state["can_forecast"])
                self.assertIn("Обучаем модель", state["message"])
            finally:
                release.set()
                worker.join(10)
        self.assertFalse(worker.is_alive())
        with Session(self.engine) as session:
            self.assertTrue(manager.snapshot(session)["can_forecast"])


if __name__ == "__main__":
    unittest.main()
