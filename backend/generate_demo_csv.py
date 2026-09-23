"""Generate small, explicitly synthetic CSVs for a clean-install smoke test."""

import argparse
import csv
import math
from datetime import datetime, timedelta
from pathlib import Path

from .app.pipeline import SOURCE_COLUMNS


def generate(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    start = datetime(2025, 10, 1)
    for turbine_id in (1, 2):
        with (directory / f"demo-turbine-{turbine_id}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(SOURCE_COLUMNS)
            for offset in range(1500):
                stamp = start + timedelta(hours=offset)
                wind = max(0.0, 6 + 3 * math.sin(offset / 17) + turbine_id * 0.4)
                power = min(1.0, max(0.0, ((wind - 3) / 11) ** 3))
                temperature = 8 + 12 * math.sin(offset / 80)
                writer.writerow((offset + 1, stamp.strftime("%Y-%m-%d %H:%M:%S"),
                                 f"{wind:.4f}", f"{power:.4f}", f"{temperature:.4f}"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    generate(parser.parse_args().directory)
