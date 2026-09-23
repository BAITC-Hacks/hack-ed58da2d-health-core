import argparse
from pathlib import Path

from .app.db import SessionLocal, init_db
from .app.pipeline import import_csv, train_model


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    importer = commands.add_parser("import-csv")
    importer.add_argument("turbine_id", type=int, choices=(1, 2))
    importer.add_argument("path", type=Path)
    commands.add_parser("train")
    args = parser.parse_args()
    init_db()
    if args.command == "init-db":
        print("Database ready")
    else:
        with SessionLocal() as session:
            if args.command == "import-csv":
                print(f"Imported {import_csv(session, args.path, args.turbine_id)} rows")
            elif args.command == "train":
                print(train_model(session))


if __name__ == "__main__":
    main()
