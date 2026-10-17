"""Request-local measurements; never carry rows, source or credentials."""
from contextvars import ContextVar

execution_metrics = ContextVar("codebound_execution_metrics", default=None)
