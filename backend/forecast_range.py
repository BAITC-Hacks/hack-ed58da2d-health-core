"""Reproducible, resumable February 2026 forecast run."""

import argparse
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from .app.db import ForecastRun, ModelRevision, SessionLocal
from .app.pipeline import create_forecast


def forecast_range(start: date, end: date, revision_id: int, turbine_ids: list[int]) -> None:
    if not date(2026, 2, 1) <= start <= end <= date(2026, 2, 28):
        raise ValueError("The test forecast range must be within February 2026")
    if not turbine_ids or len(turbine_ids) != len(set(turbine_ids)):
        raise ValueError("Select unique turbines")
    with SessionLocal() as session:
        revision = session.get(ModelRevision, revision_id)
        if revision is None:
            raise ValueError("Model revision not found")
        current = start
        while current <= end:
            existing = session.scalars(select(ForecastRun).where(
                ForecastRun.issued_at == datetime.combine(current, time.min),
                ForecastRun.model_version == revision.version,
                ForecastRun.status == "complete",
            )).all()
            complete = next((run for run in existing
                             if set(run.analysis.get("turbine_ids", [])) == set(turbine_ids)
                             and all(sum(p.turbine_id == tid for p in run.points) == 48
                                     for tid in turbine_ids)), None)
            if complete:
                print(f"{current}: existing #{complete.id}, {len(complete.points)} points", flush=True)
            else:
                run = create_forecast(session, current, turbine_ids, revision_id)
                print(f"{current}: created #{run.id}, {len(run.points)} points", flush=True)
            current += timedelta(days=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 2, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 2, 28))
    parser.add_argument("--revision-id", type=int, required=True)
    parser.add_argument("--turbine-ids", type=int, nargs="+", default=[1, 2])
    args = parser.parse_args()
    forecast_range(args.start, args.end, args.revision_id, args.turbine_ids)
