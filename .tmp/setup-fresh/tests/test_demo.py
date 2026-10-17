import csv
from datetime import date

import pytest
from fastapi.testclient import TestClient

from codebound.legacy.generate_data import generate_dataset
from codebound.main import create_app
from codebound.auth import Principal


def tenant_client(tmp_path):
    return TestClient(create_app(tmp_path, mock_tokens={"test-a": Principal("demo-tenant-1")}),
                      headers={"Authorization": "Bearer test-a"})


@pytest.fixture
def client(tmp_path):
    # Hand-calculated boundary fixture: only the two September 2026 rows count.
    with (tmp_path / "railway.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target)
        writer.writerow(["ticket_id", "travel_date", "origin", "destination", "price_cents", "tenant_id"])
        for index, (day, price) in enumerate([
            ("2026-08-31", 999), ("2026-09-01", 1234), ("2026-09-30", 5678),
            ("2026-10-01", 999), ("2025-09-15", 999),
        ]):
            writer.writerow([index, day, "Berlin", "Hamburg", price, "demo-tenant-1"])
    return tenant_client(tmp_path)


def test_dataset_list(client):
    response = client.get("/datasets")
    assert response.status_code == 200
    assert response.json() == {"datasets": [
        {"id": "railway-demo", "name": "Deutsche Bahn demo tickets", "currency": "EUR"}
    ]}


def test_september_boundaries_and_compact_result(client):
    response = client.get("/datasets/railway-demo/aggregations/september?year=2026")
    assert response.status_code == 200
    assert response.json() == {"dataset_id": "railway-demo", "year": 2026, "month": 9,
                               "ticket_count": 2, "total_revenue_cents": 6912, "currency": "EUR"}
    assert len(response.content) < 250


def test_empty_period(client):
    result = client.get("/datasets/railway-demo/aggregations/september?year=2024").json()
    assert result["ticket_count"] == result["total_revenue_cents"] == 0


@pytest.mark.parametrize("year", ["0", "9999", "invalid", "__import__('os')"])
def test_invalid_year_rejected(client, year):
    assert client.get("/datasets/railway-demo/aggregations/september", params={"year": year}).status_code == 422


@pytest.mark.parametrize("dataset_id", ["other-tenant", "railway.csv", "..%5Csecret", "__import__('os')"])
def test_unregistered_dataset_rejected(client, dataset_id):
    response = client.get(f"/datasets/{dataset_id}/aggregations/september")
    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found"}


def test_missing_data(tmp_path):
    client = tenant_client(tmp_path)
    assert client.get("/datasets").json() == {"datasets": []}
    assert client.get("/datasets/railway-demo/aggregations/september").status_code == 404


def test_no_legacy_script_execution_endpoint(client):
    assert client.post("/scripts/execute", json={"code": "print('unsafe')"}).status_code == 405


def test_generator_deterministic_and_valid(tmp_path):
    path = generate_dataset(tmp_path, rows=365)
    original = path.read_bytes()
    assert generate_dataset(tmp_path, rows=365).read_bytes() == original
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 365
    assert len({row["ticket_id"] for row in rows}) == 365
    assert rows[0]["travel_date"] == "2026-01-01"
    assert rows[-1]["travel_date"] == "2026-12-31"
    assert all(1000 <= int(row["price_cents"]) <= 15000 for row in rows)
    assert set(rows[0]) == {"ticket_id", "tenant_id", "customer_id", "booking_date",
                            "travel_date", "ticket_type", "origin", "destination", "price_cents"}
    assert {row["tenant_id"] for row in rows} == {"demo-tenant-1", "demo-tenant-2"}
    assert all(row["customer_id"].startswith("demo-customer-") for row in rows)
    assert {row["ticket_type"] for row in rows} == {"standard", "first_class", "flex"}
    assert all(date.fromisoformat(row["booking_date"]) <= date.fromisoformat(row["travel_date"])
               for row in rows)
    result = tenant_client(tmp_path).get(
        "/datasets/railway-demo/aggregations/september").json()
    assert result["ticket_count"] == 15
    assert result["total_revenue_cents"] == sum(
        int(row["price_cents"]) for row in rows
        if row["travel_date"].startswith("2026-09-") and row["tenant_id"] == "demo-tenant-1")


@pytest.mark.parametrize("rows,year", [(-1, 2026), (1_000_001, 2026), (1, 0), (1, 9999)])
def test_generator_rejects_invalid_parameters(tmp_path, rows, year):
    with pytest.raises(ValueError):
        generate_dataset(tmp_path, rows=rows, year=year)


def test_header_only_dataset(tmp_path):
    generate_dataset(tmp_path, rows=0)
    response = tenant_client(tmp_path).get("/datasets/railway-demo/aggregations/september")
    assert response.status_code == 200
    assert response.json()["ticket_count"] == response.json()["total_revenue_cents"] == 0


@pytest.mark.parametrize("content", [
    "", "wrong_column\nsecret-local-content\n",
    "tenant_id,travel_date,price_cents\ndemo-tenant-1,not-a-date,100\n",
    "tenant_id,travel_date,price_cents\ndemo-tenant-1,2026-09-01,invalid\n",
    "tenant_id,travel_date,price_cents\ndemo-tenant-1,2026-09-01,-1\n",
    "tenant_id,travel_date,price_cents\ndemo-tenant-1,2026-09-01\n",
])
def test_corrupt_dataset_returns_safe_error(tmp_path, content):
    (tmp_path / "railway.csv").write_text(content, encoding="utf-8")
    response = tenant_client(tmp_path).get("/datasets/railway-demo/aggregations/september")
    assert response.status_code == 503
    assert response.json() == {"detail": "Dataset unavailable"}


def test_booking_date_does_not_change_travel_date_filter(tmp_path):
    (tmp_path / "railway.csv").write_text(
        "tenant_id,booking_date,travel_date,price_cents\ndemo-tenant-1,2026-08-25,2026-09-01,1200\n"
        "demo-tenant-1,2026-09-25,2026-10-01,9900\n", encoding="utf-8")
    response = tenant_client(tmp_path).get("/datasets/railway-demo/aggregations/september")
    assert response.status_code == 200
    assert response.json()["ticket_count"] == 1
    assert response.json()["total_revenue_cents"] == 1200


def test_response_does_not_grow_with_dataset(tmp_path):
    generate_dataset(tmp_path, rows=12_000)
    response = tenant_client(tmp_path).get("/datasets/railway-demo/aggregations/september")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert len(response.content) < 250
    assert set(response.json()) == {"dataset_id", "year", "month", "ticket_count",
                                    "total_revenue_cents", "currency"}


def test_wrong_method_rejected(client):
    assert client.post("/datasets").status_code == 405


@pytest.mark.parametrize("year,days", [(1, 365), (2024, 366)])
def test_generator_calendar_boundaries(tmp_path, year, days):
    path = generate_dataset(tmp_path, rows=days + 1, year=year)
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert rows[days - 1]["travel_date"] == date(year, 12, 31).isoformat()
    assert rows[days]["travel_date"] == date(year, 1, 1).isoformat()
    assert all(date.fromisoformat(row["booking_date"]) <= date.fromisoformat(row["travel_date"])
               for row in rows)


def test_non_utf8_dataset_returns_safe_error(tmp_path):
    (tmp_path / "railway.csv").write_bytes(b"\xff\xfe")
    response = tenant_client(tmp_path).get("/datasets/railway-demo/aggregations/september")
    assert response.status_code == 503
    assert response.json() == {"detail": "Dataset unavailable"}
