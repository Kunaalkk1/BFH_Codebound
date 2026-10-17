"""Generate deterministic synthetic tickets; contains no real passenger data."""

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path
from random import Random

from codebound.datasets import DEFAULT_DATA_DIR


def generate_dataset(data_dir: Path, rows: int = 1200, year: int = 2026) -> Path:
    if not 0 <= rows <= 1_000_000:
        raise ValueError("rows must be between 0 and 1000000")
    if not 1 <= year <= 9998:
        raise ValueError("year must be between 1 and 9998")
    data_dir.mkdir(parents=True, exist_ok=True)
    output = data_dir / "railway.csv"
    rng = Random(42)
    start = date(year, 1, 1)
    days = (date(year + 1, 1, 1) - start).days
    routes = [("Berlin", "Hamburg"), ("Munich", "Frankfurt"), ("Cologne", "Berlin")]
    with output.open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target)
        writer.writerow(["ticket_id", "tenant_id", "customer_id", "booking_date", "travel_date",
                         "ticket_type", "origin", "destination", "price_cents"])
        for index in range(rows):
            origin, destination = routes[index % len(routes)]
            travel_date = start + timedelta(days=index % days)
            advance_days = min(index % 14, travel_date.toordinal() - 1)
            writer.writerow([f"T{index + 1:07d}", f"demo-tenant-{index % 2 + 1}",
                             f"demo-customer-{index % 100 + 1:03d}",
                             (travel_date - timedelta(days=advance_days)).isoformat(),
                             travel_date.isoformat(), ("standard", "first_class", "flex")[index % 3],
                             origin, destination, rng.randrange(1000, 15001)])
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1200)
    parser.add_argument("--year", type=int, default=2026)
    args = parser.parse_args()
    print(generate_dataset(DEFAULT_DATA_DIR, args.rows, args.year))


if __name__ == "__main__":
    main()
