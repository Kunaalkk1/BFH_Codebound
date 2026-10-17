"""Trusted server-side data broker. Only fixed tenant-to-tool mappings are used."""

import json
from codebound.datasets import DatasetUnavailableError
from codebound.mcp_client import IntegrationError, McpClient


def tool_value(response):
    if response.get("isError"):
        raise IntegrationError("Connector tool failed")
    if "structuredContent" in response:
        return response["structuredContent"]
    content = response.get("content", [])
    if len(content) != 1 or content[0].get("type") != "text":
        raise IntegrationError("Unexpected connector result")
    try:
        return json.loads(content[0]["text"])
    except (ValueError, KeyError):
        raise IntegrationError("Unexpected connector result") from None


class ConnectorBroker:
    def __init__(self, configuration, max_bytes):
        self.configuration, self.max_bytes = configuration, max_bytes

    def datasets(self, tenant_id):
        entry = self.configuration.get(tenant_id)
        return [] if not entry else [{"id": entry["reference"], "name": "AnythingMCP PostgreSQL demo",
                                      "kind": "connector", "currency": "EUR"}]

    def schema(self, tenant_id, reference):
        entry = self.configuration.get(tenant_id)
        if entry is None or reference != entry["reference"]:
            raise KeyError("Dataset not found")
        if not entry.get("schema_tool"):
            raise DatasetUnavailableError()
        try:
            from codebound.metrics import execution_metrics
            measurements = execution_metrics.get()
            if measurements is not None:
                measurements["postgres_schema_calls"] = measurements.get("postgres_schema_calls", 0) + 1
            client = McpClient(entry["endpoint"], entry["key"])
            client.initialize()
            value = tool_value(client.call(entry["schema_tool"], {}))
            from codebound.llm.service import TYPES
            rows = value["rows"]
            columns = {row["column_name"]: row["data_type"] for row in rows}
            if columns != TYPES or len(rows) != len(TYPES) or value.get("truncated"):
                raise DatasetUnavailableError()
            return {"contract_version": 1, "columns": columns}
        except (IntegrationError, ValueError, TypeError, KeyError):
            raise DatasetUnavailableError() from None

    def fetch(self, tenant_id, reference):
        entry = self.configuration.get(tenant_id)
        if entry is None or reference != entry["reference"]:
            raise KeyError("Dataset not found")
        try:
            from codebound.metrics import execution_metrics
            measurements = execution_metrics.get()
            if measurements is not None:
                measurements["postgres_data_calls"] = measurements.get("postgres_data_calls", 0) + 1
            client = McpClient(entry["endpoint"], entry["key"])
            client.initialize()
            value = tool_value(client.call(entry["tool"], {}))
            rows = value["rows"]
            if (not isinstance(rows, list) or value.get("truncated") or value.get("totalRows") != len(rows)
                    or len(json.dumps(rows).encode()) > self.max_bytes):
                raise DatasetUnavailableError()
            expected = {"ticket_id", "tenant_id", "travel_date", "ticket_type", "price_cents"}
            for row in rows:
                if not isinstance(row, dict) or set(row) != expected or row["tenant_id"] != tenant_id:
                    raise DatasetUnavailableError()
                if type(row["price_cents"]) is not int or row["price_cents"] < 0:
                    raise DatasetUnavailableError()
            return rows
        except (IntegrationError, ValueError, TypeError, KeyError):
            raise DatasetUnavailableError() from None
