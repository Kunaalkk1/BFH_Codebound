"""Server-owned dataset registry and a fixed, streaming aggregation."""

import csv
from datetime import date
from pathlib import Path

DATASET_ID = "railway-demo"
DATASETS = {"demo-tenant-1": (DATASET_ID, "Deutsche Bahn demo tickets"),
            "demo-tenant-2": ("lufthansa-demo", "Lufthansa demo railway benchmark")}
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class DatasetUnavailableError(Exception):
    """The registered local dataset cannot be read or has invalid content."""


def list_datasets(data_dir: Path, tenant_id: str) -> list[dict]:
    if not (data_dir / "railway.csv").is_file():
        return []
    entry = DATASETS.get(tenant_id)
    return [{"id": entry[0], "name": entry[1], "currency": "EUR"}] if entry else []


def september_totals(data_dir: Path, dataset_id: str, year: int, *, tenant_id: str,
                     ticket_type: str | None = None, month: int | None = 9) -> dict:
    # Never interpret client input as a filename or executable expression.
    if tenant_id not in DATASETS or dataset_id != DATASETS[tenant_id][0]:
        raise KeyError(dataset_id)
    count = revenue = 0
    try:
        with (data_dir / "railway.csv").open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            required = {"tenant_id", "travel_date", "price_cents"}
            if ticket_type is not None:
                required.add("ticket_type")
            if not required.issubset(reader.fieldnames or []):
                raise DatasetUnavailableError()
            for row in reader:
                if not row["tenant_id"]:
                    raise DatasetUnavailableError()
                if row["tenant_id"] != tenant_id:
                    continue
                travel_date = date.fromisoformat(row["travel_date"])
                price = int(row["price_cents"])
                if price < 0:
                    raise ValueError("Negative ticket price")
                if (travel_date.year == year and (month is None or travel_date.month == month)
                        and (ticket_type is None or row["ticket_type"] == ticket_type)):
                    count += 1
                    revenue += price
    except FileNotFoundError:
        raise
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        raise DatasetUnavailableError() from exc
    return {"dataset_id": dataset_id, "year": year, "month": month,
            "ticket_count": count, "total_revenue_cents": revenue, "currency": "EUR"}
