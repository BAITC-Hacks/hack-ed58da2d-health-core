import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db import init_db
from backend.app.main import app, get_session
from backend.app.pipeline import SOURCE_COLUMNS


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict("os.environ", {"API_WRITE_KEY": "", "APP_ENV": "development"})
        self.env_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{(Path(self.temp.name) / 'api.sqlite3').as_posix()}",
                                    connect_args={"check_same_thread": False})
        init_db(self.engine)

        def local_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = local_session
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp.cleanup()
        self.env_patch.stop()

    def test_new_turbine_csv_upload_and_model_validation(self):
        turbine = self.client.post("/turbines", json={"name": "Турбина 03", "latitude": 43.7,
                                                      "longitude": 78.6})
        self.assertEqual(turbine.status_code, 201)
        turbine_id = turbine.json()["id"]
        self.assertEqual(len(self.client.get("/turbines").json()), 3)
        body = (",".join(SOURCE_COLUMNS) + "\n1,2023-03-11 0:00:00,6.73,0.39,15.38\n").encode()
        for expected in (1, 0):
            uploaded = self.client.post(f"/measurements/upload?turbine_id={turbine_id}",
                                        files={"file": ("training.csv", body, "text/csv")})
            self.assertEqual(uploaded.status_code, 201)
            self.assertEqual(uploaded.json()["imported"], expected)
        rows = self.client.get(f"/measurements?turbine_id={turbine_id}").json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["original"][SOURCE_COLUMNS[2]], "6.73")
        self.assertEqual(self.client.post("/models/train").status_code, 422)
        self.assertEqual(self.client.post("/models/train", json={"cutoff_date": "2026-02-02"}).status_code, 422)

    def test_operator_key_protects_writes_without_blocking_reads(self):
        self.assertEqual(self.client.get('/forecasts').json(), [])
        with patch.dict("os.environ", {"API_WRITE_KEY": "test-operator-secret"}):
            self.assertEqual(self.client.get("/turbines").status_code, 200)
            body = {"name": "Турбина 03", "latitude": 43.7, "longitude": 78.6}
            self.assertEqual(self.client.post("/turbines", json=body).status_code, 401)
            self.assertEqual(self.client.post("/turbines", json=body,
                                              headers={"X-Admin-Key": "wrong"}).status_code, 401)
            self.assertEqual(self.client.post("/turbines", json=body,
                                              headers={"X-Admin-Key": "test-operator-secret"}).status_code, 201)


if __name__ == "__main__":
    unittest.main()
