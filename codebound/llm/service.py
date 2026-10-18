"""Tenant authorization -> schema only -> reuse/generate -> save. No execution."""

import csv
import json
from threading import Lock
from time import perf_counter

from codebound.auth import TENANTS
from codebound.datasets import DATASETS
from codebound.llm.client import AIError, OllamaClient
from codebound.llm.prompts import prompt
from codebound.llm.store import AIStore
from codebound.llm.validator import validate_source

TYPES = {"ticket_id": "text", "tenant_id": "text", "travel_date": "text",
         "ticket_type": "text", "price_cents": "integer"}


class AIService:
    def __init__(self, store, data_dir, broker=None, client=None):
        self.store, self.data_dir, self.broker = AIStore(store), data_dir, broker
        self.client = client if client is not None else OllamaClient()
        self.locks = {tenant: Lock() for tenant in TENANTS}

    def schema(self, tenant, reference):
        if reference.kind == "connector":
            if self.broker is None:
                raise AIError("CONNECTOR_UNAVAILABLE")
            try:
                return self.broker.schema(tenant, reference.id)
            except KeyError:
                raise AIError("DATASET_NOT_FOUND", 404) from None
        if tenant not in DATASETS or reference.id != DATASETS[tenant][0]:
            raise AIError("DATASET_NOT_FOUND", 404)
        try:
            with (self.data_dir / "railway.csv").open(newline="", encoding="utf-8") as file:
                fields = csv.DictReader(file).fieldnames
            if not set(TYPES).issubset(fields or []):
                raise AIError("SCHEMA_UNAVAILABLE")
        except OSError:
            raise AIError("SCHEMA_UNAVAILABLE") from None
        # Only the approved columns; additional CSV columns are not sent to Ollama.
        return {"contract_version": 1, "columns": TYPES}

    def generate(self, tenant, body, metrics, *, reuse=False):
        schema = self.schema(tenant, body.reference)  # Authorize before any LLM request.
        reference, parameters = body.reference.model_dump(), body.parameters.model_dump()
        started = perf_counter()
        record = self.store.reusable(tenant, body.task, reference, schema, parameters) if reuse else None
        metrics["lookup_ms"] = round((perf_counter() - started) * 1000, 3)
        if record:
            metrics["script_reused"] = True
            context = self.store.context(tenant, record["script_id"])
            return record, context["model"]
        source = self.client.generate(prompt(body.task, schema, parameters), metrics)
        validate_source(source)
        record = self.store.saved(tenant, body.task, reference, schema, parameters, source, self.client.settings.model, metrics)
        return record, self.client.settings.model

    def modify(self, tenant, script_id, body, metrics):
        previous = self.store.store.get(tenant, script_id)
        if previous is None:
            raise AIError("SCRIPT_NOT_FOUND", 404)
        if body.expected_version is not None and body.expected_version != previous["version"]:
            raise AIError("SCRIPT_VERSION_CONFLICT", 409)
        context = self.store.context(tenant, script_id)
        if context is None:
            raise AIError("SCRIPT_HAS_NO_AI_DATASET_CONTEXT", 409)
        from codebound.models import Reference
        reference = json.loads(context["reference_json"])
        schema = self.schema(tenant, Reference.model_validate(reference))
        parameters = json.loads(context["parameters_json"])
        source = self.client.generate(prompt(body.instruction, schema, parameters, previous["source_code"]), metrics)
        validate_source(source)
        if source == previous["source_code"]:
            raise AIError("SOURCE_UNCHANGED", 422)
        # New purpose is NOT reusable as the original task; source edits invalidate old signatures.
        task = context["task_text"] + "\nModification: " + body.instruction
        record = self.store.saved(tenant, task, reference, schema, parameters, source, self.client.settings.model, metrics, previous=previous)
        return record, self.client.settings.model
