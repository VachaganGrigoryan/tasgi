"""Schema inference and simple model coercion for tasgi."""

from __future__ import annotations

import dataclasses
import inspect
import json
import sys
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

JSONSchema = dict[str, Any]
_UNION_ORIGINS = tuple(origin for origin in (Union, getattr(types, "UnionType", None)) if origin is not None)

_PRIMITIVE_SCHEMAS = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    bytes: {"type": "string", "format": "binary"},
}


def infer_json_schema(annotation: Any) -> JSONSchema:
    """Infer a small JSON Schema subset from a Python annotation."""

    annotation = _unwrap_annotated(annotation)
    if annotation in _PRIMITIVE_SCHEMAS:
        return dict(_PRIMITIVE_SCHEMAS[annotation])
    if annotation is Any or annotation is object:
        return {}
    if annotation is type(None):
        return {"type": "null"}
    if _is_typed_dict(annotation):
        return _typed_dict_schema(annotation)
    if dataclasses.is_dataclass(annotation):
        return _dataclass_schema(annotation)

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {list, tuple}:
        item_schema = infer_json_schema(args[0]) if args else {}
        return {"type": "array", "items": item_schema}
    if origin is dict:
        value_schema = infer_json_schema(args[1]) if len(args) >= 2 else {}
        return {"type": "object", "additionalProperties": value_schema}
    if origin in _UNION_ORIGINS:
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1 and len(non_none) != len(args):
            schema = infer_json_schema(non_none[0])
            schema["nullable"] = True
            return schema
        return {"anyOf": [infer_json_schema(arg) for arg in args]}

    return {"type": "object"}


def decode_request_model(request, annotation: Any) -> Any:
    """Decode a request body into a simple typed model."""

    annotation = _unwrap_annotated(annotation)
    if annotation is bytes:
        return request.body
    if annotation is str:
        return request.text()

    if not request.body:
        payload: Any = None
    else:
        payload = json.loads(request.text())
    return _coerce_value(payload, annotation)


def serialize_model_value(value: Any) -> Any:
    """Convert a typed model value into JSON-serializable data."""

    if dataclasses.is_dataclass(value):
        return {field.name: serialize_model_value(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): serialize_model_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_model_value(item) for item in value]
    return value


def get_callable_type_hints(func: Any) -> dict[str, Any]:
    """Return resolved type hints for one callable, including closure locals."""

    globalns = getattr(func, "__globals__", None)
    localns: dict[str, Any] = {}
    try:
        closure = inspect.getclosurevars(func)
    except TypeError:
        closure = None
    if closure is not None:
        localns.update(closure.globals)
        localns.update(closure.nonlocals)
    return _safe_get_type_hints(func, globalns=globalns, localns=localns)


def get_model_type_hints(annotation: Any) -> dict[str, Any]:
    """Return resolved type hints for one model type."""

    module = sys.modules.get(getattr(annotation, "__module__", ""))
    globalns = vars(module) if module is not None else None
    localns = {getattr(annotation, "__name__", ""): annotation}
    return _safe_get_type_hints(annotation, globalns=globalns, localns=localns)


def _coerce_value(value: Any, annotation: Any) -> Any:
    annotation = _unwrap_annotated(annotation)
    if annotation is Any or annotation is object:
        return value
    if annotation is bytes:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        return str(value).encode("utf-8")
    if annotation is str:
        return str(value)
    if annotation in {int, float, bool}:
        return annotation(value)
    if annotation is type(None):
        return None
    if _is_typed_dict(annotation):
        return _coerce_typed_dict(value, annotation)
    if dataclasses.is_dataclass(annotation):
        if not isinstance(value, dict):
            raise TypeError("Dataclass request bodies must decode from JSON objects.")
        field_types = get_model_type_hints(annotation)
        values: dict[str, Any] = {}
        for field in dataclasses.fields(annotation):
            if field.name in value:
                values[field.name] = _coerce_value(
                    value[field.name],
                    field_types.get(field.name, field.type),
                )
        return annotation(**values)

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {list, tuple}:
        item_type = args[0] if args else Any
        return [_coerce_value(item, item_type) for item in list(value or [])]
    if origin is dict:
        value_type = args[1] if len(args) >= 2 else Any
        return {str(key): _coerce_value(item, value_type) for key, item in dict(value or {}).items()}
    if origin in _UNION_ORIGINS:
        non_none = [arg for arg in args if arg is not type(None)]
        if value is None and len(non_none) != len(args):
            return None
        for candidate in non_none or args:
            try:
                return _coerce_value(value, candidate)
            except Exception:
                continue
        raise TypeError("Unable to coerce value to union annotation %r." % (annotation,))

    return value


def _dataclass_schema(annotation: Any) -> JSONSchema:
    field_types = get_model_type_hints(annotation)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in dataclasses.fields(annotation):
        properties[field.name] = infer_json_schema(field_types.get(field.name, field.type))
        if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
            required.append(field.name)
    schema: JSONSchema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _typed_dict_schema(annotation: Any) -> JSONSchema:
    field_types = get_model_type_hints(annotation) or annotation.__annotations__
    properties = {
        name: infer_json_schema(field_type)
        for name, field_type in field_types.items()
    }
    required = sorted(annotation.__required_keys__) if hasattr(annotation, "__required_keys__") else []
    schema: JSONSchema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _coerce_typed_dict(value: Any, annotation: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("TypedDict request bodies must decode from JSON objects.")
    field_types = get_model_type_hints(annotation) or annotation.__annotations__
    return {
        key: _coerce_value(value[key], field_type)
        for key, field_type in field_types.items()
        if key in value
    }


def _unwrap_annotated(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    if str(origin) == "<class 'typing.Annotated'>":
        args = get_args(annotation)
        return args[0] if args else annotation
    return annotation


def _is_typed_dict(annotation: Any) -> bool:
    return isinstance(annotation, type) and type(annotation).__name__ == "_TypedDictMeta"


def _safe_get_type_hints(obj: Any, *, globalns=None, localns=None) -> dict[str, Any]:
    try:
        return get_type_hints(obj, globalns=globalns, localns=localns)
    except Exception:
        return {}
