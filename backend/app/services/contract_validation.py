"""Data contract schema parsing and record validation — D16.

Contract schema_json format:
    {
      "fields": [
        {"name": "value", "type": "float", "nullable": false,
         "required": true, "min": 0, "max": 1e6, "unit": "USD/t"}
      ],
      "additional_fields": true      # false → unknown fields are violations
    }

Pure functions — no persistence. DataContractManager applies them to datasets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

_NUMBER_TYPES = {"float", "number"}
_INT_TYPES = {"int", "integer"}
_TYPE_ALIASES = {
    "float": "number", "number": "number",
    "int": "integer", "integer": "integer",
    "str": "string", "string": "string",
    "bool": "boolean", "boolean": "boolean",
    "object": "object", "dict": "object",
    "array": "array", "list": "array",
}


class ContractSchemaError(ValueError):
    """The contract schema itself is malformed."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str                 # canonical type name
    nullable: bool = False
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class ContractSchema:
    fields: tuple[FieldSpec, ...] = ()
    additional_fields: bool = True

    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


@dataclass(frozen=True)
class ContractViolation:
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


class ContractViolationError(Exception):
    """A record does not satisfy the contract of the dataset it targets."""

    def __init__(
        self,
        *,
        dataset_id: str,
        dataset_name: str,
        contract_semver: str,
        phase: str,                     # "input" | "output" | "write"
        violations: list[ContractViolation],
    ) -> None:
        self.dataset_id = dataset_id
        self.dataset_name = dataset_name
        self.contract_semver = contract_semver
        self.phase = phase
        self.violations = list(violations)
        details = "; ".join(str(v) for v in self.violations)
        super().__init__(
            f"{phase.capitalize()} contract violation on dataset "
            f"{dataset_name!r} (contract {contract_semver}): {details}"
        )


def parse_schema(schema: Any) -> ContractSchema:
    """Parse a decoded schema_json document; raise ContractSchemaError if malformed."""
    if not isinstance(schema, dict):
        raise ContractSchemaError("Contract schema must be a JSON object.")
    raw_fields = schema.get("fields", [])
    if not isinstance(raw_fields, list):
        raise ContractSchemaError("'fields' must be a list.")

    specs: list[FieldSpec] = []
    seen: set[str] = set()
    for i, f in enumerate(raw_fields):
        if not isinstance(f, dict):
            raise ContractSchemaError(f"fields[{i}] must be an object.")
        name = f.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ContractSchemaError(f"fields[{i}] requires a non-empty 'name'.")
        if name in seen:
            raise ContractSchemaError(f"Duplicate field name {name!r}.")
        seen.add(name)
        ftype = _TYPE_ALIASES.get(str(f.get("type", "")).lower())
        if ftype is None:
            raise ContractSchemaError(
                f"Field {name!r} has unsupported type {f.get('type')!r}; "
                f"supported: {sorted(set(_TYPE_ALIASES))}."
            )
        minimum, maximum = f.get("min"), f.get("max")
        for label, bound in (("min", minimum), ("max", maximum)):
            if bound is not None:
                if ftype not in ("number", "integer"):
                    raise ContractSchemaError(f"Field {name!r}: '{label}' only applies to numeric types.")
                if isinstance(bound, bool) or not isinstance(bound, (int, float)):
                    raise ContractSchemaError(f"Field {name!r}: '{label}' must be numeric.")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ContractSchemaError(f"Field {name!r}: min > max.")
        specs.append(FieldSpec(
            name=name,
            type=ftype,
            nullable=bool(f.get("nullable", False)),
            required=bool(f.get("required", True)),
            minimum=minimum,
            maximum=maximum,
            unit=f.get("unit"),
        ))

    additional = schema.get("additional_fields", True)
    if not isinstance(additional, bool):
        raise ContractSchemaError("'additional_fields' must be a boolean.")
    return ContractSchema(fields=tuple(specs), additional_fields=additional)


def _type_ok(value: Any, ftype: str) -> bool:
    if ftype == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if ftype == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if ftype == "string":
        return isinstance(value, str)
    if ftype == "boolean":
        return isinstance(value, bool)
    if ftype == "object":
        return isinstance(value, dict)
    if ftype == "array":
        return isinstance(value, list)
    return False


def validate_record(record: Any, schema: ContractSchema) -> list[ContractViolation]:
    """Return every violation of `schema` by `record` (empty list = valid)."""
    if not isinstance(record, dict):
        return [ContractViolation("<record>", f"expected an object, got {type(record).__name__}")]

    violations: list[ContractViolation] = []
    for spec in schema.fields:
        if spec.name not in record:
            if spec.required:
                violations.append(ContractViolation(spec.name, "required field is missing"))
            continue
        value = record[spec.name]
        if value is None:
            if not spec.nullable:
                violations.append(ContractViolation(spec.name, "null is not allowed"))
            continue
        if not _type_ok(value, spec.type):
            violations.append(ContractViolation(
                spec.name, f"expected {spec.type}, got {type(value).__name__}"
            ))
            continue
        if spec.type in ("number", "integer"):
            if not math.isfinite(value):
                violations.append(ContractViolation(spec.name, "must be a finite number"))
                continue
            if spec.minimum is not None and value < spec.minimum:
                violations.append(ContractViolation(spec.name, f"{value} is below minimum {spec.minimum}"))
            if spec.maximum is not None and value > spec.maximum:
                violations.append(ContractViolation(spec.name, f"{value} is above maximum {spec.maximum}"))

    if not schema.additional_fields:
        declared = set(schema.field_names())
        for key in record:
            if key not in declared:
                violations.append(ContractViolation(key, "field is not declared in the contract"))
    return violations


def coerce_record(record: dict[str, Any], schema: ContractSchema) -> dict[str, Any]:
    """Canonical representation of a *valid* record: integers in number fields become floats,
    so 100 and 100.0 hash identically."""
    out = dict(record)
    for spec in schema.fields:
        value = out.get(spec.name)
        if spec.type == "number" and isinstance(value, int) and not isinstance(value, bool):
            out[spec.name] = float(value)
    return out


def semver_key(semver: str) -> tuple:
    parts = []
    for p in semver.split("."):
        parts.append((0, int(p)) if p.isdigit() else (1, p))
    return tuple(parts)
