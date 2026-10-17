"""Bounded native Ollama API requests, fixed loopback destination, no cloud fallback."""

from dataclasses import dataclass
import http.client
import json
import os
from threading import BoundedSemaphore
from time import monotonic, perf_counter

from pydantic import ValidationError
from codebound.llm.models import GeneratedSource


class AIError(Exception):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class OllamaSettings:
    model: str = ""
    generation_timeout: float = 24
    connect_timeout: float = 3
    max_prompt_bytes: int = 24000
    max_response_bytes: int = 131072

    def __post_init__(self):
        if not 1 <= self.generation_timeout <= 24 or not 0 < self.connect_timeout <= 5:
            raise ValueError("Ollama timeout outside local gateway budget")
        if self.model and (len(self.model) > 120 or not all(c.isalnum() or c in '.:/_-' for c in self.model)):
            raise ValueError("Invalid configured local model identifier")

    @classmethod
    def from_env(cls):
        return cls(model=os.getenv("OLLAMA_MODEL", ""))


class OllamaClient:
    def __init__(self, settings=None):
        self.settings = settings or OllamaSettings.from_env()
        self.slot = BoundedSemaphore(1)

    def request(self, method, path, body=None, timeout=3):
        from codebound.metrics import execution_metrics
        measurements = execution_metrics.get()
        if measurements is not None:
            measurements["ollama_api_requests"] = measurements.get("ollama_api_requests", 0) + 1
        # No request URL, proxy, redirect or inference provider comes from the caller.
        connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=self.settings.connect_timeout)
        deadline = monotonic() + timeout
        try:
            connection.connect()
            transport = connection.sock
            connection.sock.settimeout(max(.01, deadline - monotonic()))
            connection.request(method, path, body=None if body is None else json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise AIError("OLLAMA_REQUEST_FAILED")
            output = bytearray()
            while not response.isclosed():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise AIError("OLLAMA_TIMEOUT", 504)
                # HTTPResponse may retain the socket after HTTPConnection detaches it.
                transport.settimeout(remaining)
                chunk = response.read1(min(4096, self.settings.max_response_bytes + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > self.settings.max_response_bytes:
                    raise AIError("OLLAMA_RESPONSE_LIMIT", 502)
            value = json.loads(output)
            if not isinstance(value, dict):
                raise AIError("OLLAMA_INVALID_RESPONSE", 502)
            return value
        except (TimeoutError,):
            raise AIError("OLLAMA_TIMEOUT", 504) from None
        except (OSError, http.client.HTTPException):
            raise AIError("OLLAMA_UNAVAILABLE") from None
        except (ValueError, TypeError, AttributeError):
            raise AIError("OLLAMA_INVALID_RESPONSE", 502) from None
        finally:
            connection.close()

    def ready(self):
        model = self.settings.model
        if not model or model.endswith("-cloud"):
            raise AIError("LOCAL_MODEL_NOT_CONFIGURED")
        response = self.request("GET", "/api/tags")
        entries = response.get("models")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise AIError("OLLAMA_INVALID_RESPONSE", 502)
        for entry in entries:
            if entry.get("name") == model:
                if (entry.get("remote_host") or entry.get("remote_model") or not isinstance(entry.get("digest"), str)
                        or not entry["digest"] or type(entry.get("size")) is not int or entry["size"] <= 0):
                    raise AIError("REMOTE_MODEL_FORBIDDEN", 422)
                return {"model": model, "size_bytes": entry["size"], "digest": entry["digest"]}
        raise AIError("LOCAL_MODEL_UNAVAILABLE")

    def generate(self, prompt, metrics):
        if len(prompt.encode()) > self.settings.max_prompt_bytes:
            raise AIError("PROMPT_LIMIT_EXCEEDED", 422)
        if not self.slot.acquire(blocking=False):
            raise AIError("OLLAMA_BUSY", 429)
        started = perf_counter()
        try:
            self.ready()
            metrics["ollama_calls"] = 1  # POST attempt; not a claim of completed inference.
            response = self.request("POST", "/api/generate", {
                "model": self.settings.model, "prompt": prompt, "stream": False,
                "format": GeneratedSource.model_json_schema(), "keep_alive": "10m",
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1200}},
                timeout=self.settings.generation_timeout)
            if response.get("done") is not True or response.get("done_reason", "stop") != "stop":
                raise AIError("OLLAMA_INCOMPLETE_RESPONSE", 502)
            try:
                generated = GeneratedSource.model_validate_json(response["response"])
            except (ValidationError, KeyError, TypeError):
                raise AIError("OLLAMA_INVALID_SOURCE_RESPONSE", 502) from None
            for field in ("prompt_eval_count", "eval_count", "total_duration"):
                value = response.get(field)
                if type(value) is int and value >= 0:
                    metrics[field] = value
            return generated.source_code
        finally:
            metrics["generation_ms"] = round((perf_counter() - started) * 1000, 3)
            self.slot.release()
