"""Utilidades para validar la implementación contra ``contract/openapi.yaml``."""

import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml
from jsonschema import Draft202012Validator, FormatChecker

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contract" / "openapi.yaml"
API_PREFIX = "/api/v1"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
SAMPLE_UUID = "11111111-1111-4111-8111-111111111111"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def to_json_schema(node):
    """Convierte ``nullable: true`` (estilo OpenAPI 3.0) a ``type: [t, "null"]``."""
    if isinstance(node, dict):
        out = {k: to_json_schema(v) for k, v in node.items()}
        if out.get("nullable") is True and "type" in out:
            out.pop("nullable")
            t = out["type"]
            out["type"] = [t, "null"] if isinstance(t, str) else [*t, "null"]
        return out
    if isinstance(node, list):
        return [to_json_schema(v) for v in node]
    return node


def flat(prop: dict) -> dict:
    """Aplana un ``anyOf`` con ``null`` (Optional) y normaliza ``const`` a ``enum``.

    Devuelve las restricciones mezcladas de las ramas no nulas más
    ``_nullable`` (bool) y ``_types`` (set de tipos no nulos).
    """
    branches = prop.get("anyOf", [prop])
    merged: dict = {}
    types: set = set()
    nullable = False
    for branch in branches:
        if branch.get("type") == "null":
            nullable = True
            continue
        branch = dict(branch)
        if "const" in branch:
            branch["enum"] = [branch.pop("const")]
        if "type" in branch:
            types.add(branch["type"])
        merged.update(branch)
    merged["_nullable"] = nullable
    merged["_types"] = types
    return merged


def make_format_checker() -> FormatChecker:
    checker = FormatChecker(formats=())

    @checker.checks("uuid", raises=ValueError)
    def _uuid(value):
        return not isinstance(value, str) or str(uuid.UUID(value)) == value.lower()

    @checker.checks("date-time", raises=ValueError)
    def _date_time(value):
        if not isinstance(value, str):
            return True
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None

    @checker.checks("email")
    def _email(value):
        return not isinstance(value, str) or bool(_EMAIL_RE.match(value))

    @checker.checks("uri")
    def _uri(value):
        if not isinstance(value, str):
            return True
        parsed = urlparse(value)
        return bool(parsed.scheme and parsed.netloc)

    return checker


class Contract:
    def __init__(self, path: Path = CONTRACT_PATH):
        self.doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.schemas = self.doc["components"]["schemas"]
        self._format_checker = make_format_checker()

    # -- referencias ------------------------------------------------------
    def deref(self, node):
        while isinstance(node, dict) and "$ref" in node:
            target = self.doc
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            node = target
        return node

    # -- validación -------------------------------------------------------
    def validator(self, schema: dict) -> Draft202012Validator:
        root = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "components": {"schemas": to_json_schema(self.schemas)},
            **to_json_schema(schema),
        }
        return Draft202012Validator(root, format_checker=self._format_checker)

    def errors(self, schema: dict, instance) -> list[str]:
        found = sorted(
            self.validator(schema).iter_errors(instance), key=lambda e: list(e.path)
        )
        return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in found]

    def assert_valid(self, schema: dict, instance, label: str = "") -> None:
        problems = self.errors(schema, instance)
        assert not problems, f"{label} no cumple el contrato:\n  " + "\n  ".join(
            problems
        )

    def schema_ref(self, name: str) -> dict:
        return {"$ref": f"#/components/schemas/{name}"}

    # -- operaciones ------------------------------------------------------
    def operations(self):
        """Itera ``(path, method, operation)`` del contrato."""
        for path, item in self.doc["paths"].items():
            for method in HTTP_METHODS:
                if method in item:
                    yield path, method, item[method]

    def needs_auth(self, op: dict) -> bool:
        return bool(op.get("security", self.doc.get("security", [])))

    @staticmethod
    def success_status(op: dict) -> int:
        return next(int(code) for code in op["responses"] if code.startswith("2"))

    def response_schema(self, op: dict, status: int) -> dict | None:
        response = self.deref(op["responses"][str(status)])
        content = response.get("content")
        return content["application/json"]["schema"] if content else None

    def parameters(self, path: str, method: str) -> list[dict]:
        item = self.doc["paths"][path]
        params = [*item.get("parameters", []), *item[method].get("parameters", [])]
        return [self.deref(p) for p in params]

    # -- generación de ejemplos ------------------------------------------
    def example(self, schema: dict):
        """Genera una instancia mínima válida para ``schema``."""
        s = self.deref(schema)
        if "enum" in s:
            return s["enum"][0]
        t = s.get("type")
        if isinstance(t, list):
            t = next(x for x in t if x != "null")
        if t == "object" or "properties" in s:
            props = s.get("properties", {})
            required = list(s.get("required", []))
            if not required and s.get("minProperties") and props:
                required = [next(iter(props))]
            return {name: self.example(props[name]) for name in required}
        if t == "array":
            count = max(s.get("minItems", 1), 1)
            return [self.example(s["items"]) for _ in range(count)]
        if t == "string":
            fmt = s.get("format")
            return {
                "uuid": SAMPLE_UUID,
                "email": "usuario@example.com",
                "date-time": "2026-01-01T00:00:00Z",
                "uri": "https://example.com/a",
                "password": "Passw0rd!123",
            }.get(fmt, "x" * max(s.get("minLength", 1), 1))
        if t in ("integer", "number"):
            return s.get("minimum", 1)
        if t == "boolean":
            return True
        raise ValueError(f"No sé generar un ejemplo para {schema!r}")

    def build_request(
        self, path: str, method: str, op: dict, *, with_auth: bool = True
    ) -> dict:
        """Argumentos para ``client.request`` de una petición válida."""
        kwargs: dict = {
            "method": method.upper(),
            "url": API_PREFIX + re.sub(r"\{[^}]+\}", SAMPLE_UUID, path),
            "headers": {"Authorization": "Bearer test-token"} if with_auth else {},
        }
        body = op.get("requestBody")
        if body:
            content = self.deref(body)["content"]
            if "application/json" in content:
                kwargs["json"] = self.example(content["application/json"]["schema"])
            elif "multipart/form-data" in content:
                kwargs["files"] = [
                    ("files", ("apuntes.md", b"# Apuntes", "text/markdown"))
                ]
        return kwargs


contract = Contract()
