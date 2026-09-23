"""Read-only evaluation of a saved model on its chronological holdout."""

import argparse
import io
import json
from datetime import timedelta

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import select

from .app.db import Measurement, ModelArtifact, ModelRevision, SessionLocal
from .app.pipeline import features


def evaluate(revision_id: int) -> dict:
    with SessionLocal() as session:
        revision = session.get(ModelRevision, revision_id)
        if revision is None:
            raise ValueError(f"Model revision {revision_id} not found")
        artifact = session.get(ModelArtifact, revision_id)
        if artifact is None:
            raise ValueError("Model artifact is missing from the database")
        cutoff = revision.training_max_at + timedelta(hours=1)
        rows = session.execute(select(
            Measurement.turbine_id, Measurement.observed_at, Measurement.wind_speed_ms,
            Measurement.temperature_c, Measurement.normalized_power,
        ).where(Measurement.observed_at < cutoff)).all()

    frame = pd.DataFrame(rows, columns=[
        "turbine_id", "observed_at", "wind_speed_ms", "temperature_c", "normalized_power",
    ])
    frame["observed_at"] = pd.to_datetime(frame["observed_at"])
    frame = (frame.set_index("observed_at").groupby("turbine_id")
             [["wind_speed_ms", "temperature_c", "normalized_power"]]
             .resample("h").mean().dropna().reset_index().sort_values("observed_at"))
    split_time = frame["observed_at"].quantile(0.9)
    train = frame[frame["observed_at"] < split_time]
    holdout = frame[frame["observed_at"] >= split_time].copy()
    if len(train) != revision.training_rows or len(holdout) != revision.validation_rows:
        raise ValueError("Stored revision does not match the current database training sample")
    model = joblib.load(io.BytesIO(artifact.data))["model"]
    holdout["prediction"] = np.clip(model.predict(features(holdout)), 0, 1)
    means = train.groupby("turbine_id")["normalized_power"].mean()
    holdout["baseline"] = holdout["turbine_id"].map(means)

    def metrics(part: pd.DataFrame) -> dict:
        target = part["normalized_power"].to_numpy()
        prediction = part["prediction"].to_numpy()
        baseline = part["baseline"].to_numpy()
        return {"hours": len(part),
                "mae": float(np.mean(np.abs(target - prediction))),
                "rmse": float(np.sqrt(np.mean((target - prediction) ** 2))),
                "baseline_mae": float(np.mean(np.abs(target - baseline)))}

    result = {"revision_id": revision_id, "model_version": revision.version,
              "holdout_start": holdout["observed_at"].min().isoformat(),
              "holdout_end": holdout["observed_at"].max().isoformat(),
              "overall": metrics(holdout),
              "by_turbine": {str(tid): metrics(group) for tid, group in holdout.groupby("turbine_id")}}
    if abs(result["overall"]["mae"] - revision.validation_mae) > 1e-10:
        raise ValueError("Recalculated MAE differs from stored validation MAE")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision_id", type=int)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.revision_id), ensure_ascii=False, indent=2))
