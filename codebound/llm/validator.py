"""Compatibility screening only; AST checks are NOT a security sandbox."""

import ast
from codebound.llm.client import AIError

ALLOWED_IMPORTS = {"json", "datetime", "statistics", "math", "collections", "decimal"}
FORBIDDEN = {"exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr", "input", "breakpoint"}


def validate_source(source):
    if not isinstance(source, str) or not source or len(source) > 32000 or len(source.encode()) > 64000:
        raise AIError("SOURCE_LIMIT_EXCEEDED", 422)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        raise AIError("INVALID_PYTHON_SOURCE", 422) from None
    prints, reads = 0, 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name not in ALLOWED_IMPORTS for alias in node.names):
            raise AIError("INCOMPATIBLE_IMPORT", 422)
        if isinstance(node, ast.ImportFrom) and (node.level or node.module not in ALLOWED_IMPORTS):
            raise AIError("INCOMPATIBLE_IMPORT", 422)
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise AIError("INCOMPATIBLE_SOURCE", 422)
        if isinstance(node, ast.Name) and node.id in FORBIDDEN:
            raise AIError("INCOMPATIBLE_SOURCE", 422)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "open":
                if (not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value != "/tmp/input.json"
                        or any(isinstance(arg, ast.Starred) for arg in node.args)
                        or len(node.args) > 2 or (len(node.args) > 1 and not (isinstance(node.args[1], ast.Constant) and node.args[1].value in {"r", "rt"}))
                        or any(keyword.arg not in {"encoding", "mode"} or not isinstance(keyword.value, ast.Constant)
                               or (keyword.arg == "mode" and keyword.value.value not in {"r", "rt"}) for keyword in node.keywords)):
                    raise AIError("INCOMPATIBLE_FILE_ACCESS", 422)
                reads += 1
            if node.func.id == "print":
                prints += 1
                if not (len(node.args) == 1 and isinstance(node.args[0], ast.Call)
                        and isinstance(node.args[0].func, ast.Attribute)
                        and isinstance(node.args[0].func.value, ast.Name)
                        and node.args[0].func.value.id == "json" and node.args[0].func.attr == "dumps"
                        and not node.keywords):
                    raise AIError("INVALID_WORKER_OUTPUT_CONTRACT", 422)
    if reads != 1 or prints != 1:
        raise AIError("INVALID_WORKER_IO_CONTRACT", 422)
    return source
