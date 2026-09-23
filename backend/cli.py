import argparse
from datetime import date, datetime, time
from pathlib import Path

from .app.db import SessionLocal, init_db
from .app.pipeline import import_csv, train_model


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    importer = commands.add_parser("import-csv")
    importer.add_argument("turbine_id", type=int)
    importer.add_argument("path", type=Path)
    trainer = commands.add_parser("train")
    trainer.add_argument("--cutoff-date", type=date.fromisoformat, default=date(2026, 1, 31))
    args = parser.parse_args()
    init_db()
    if args.command == "init-db":
        print("Database ready")
    else:
        with SessionLocal() as session:
            if args.command == "import-csv":
                print(f"Imported {import_csv(session, args.path, args.turbine_id)} rows")
            elif args.command == "train":
                if args.cutoff_date > date(2026, 2, 1):
                    parser.error("Training cutoff cannot enter the February test period")
                print(train_model(session, datetime.combine(args.cutoff_date, time.min)))


if __name__ == "__main__":
    main()
