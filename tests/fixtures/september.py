"""Example source: executed only inside a worker, not as a Windows demo command."""
import json
from datetime import date

with open("/tmp/input.json", encoding="utf-8") as source:
    data = json.load(source)
year = data["parameters"]["year"]
rows = [row for row in data["records"]
        if date.fromisoformat(row["travel_date"]).year == year
        and date.fromisoformat(row["travel_date"]).month == 9]
print(json.dumps({"month": "September", "ticket_count": len(rows),
                  "total_revenue_cents": sum(int(row["price_cents"]) for row in rows)}))
