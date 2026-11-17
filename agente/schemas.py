"""Esquemas Pydantic generados dinámicamente desde el catálogo YAML.

- Validación de conf_*.json (campos declarados en config_template.fields).
- Validación de parámetros de usuario (ParamSpec).
Sin código por herramienta: todo sale del YAML.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, create_model

from .models import ConfigField, ParamSpec, ToolSpec

_PY = {"string": str, "path": str, "integer": int, "number": float, "boolean": bool, "object": dict, "array": list}


def _group(fields: list[ConfigField]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for f in fields:
        node = tree
        parts = f.name.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = f
    return tree


def _build(name: str, tree: dict[str, Any]) -> type[BaseModel]:
    defs: dict[str, Any] = {}
    for key, node in tree.items():
        if isinstance(node, ConfigField):
            ptype = _PY[node.type]
            defs[key] = (ptype, ...) if node.required else (Optional[ptype], None)
        else:
            sub = _build(f"{name}_{key}", node)
            required = any(_has_required(node))
            defs[key] = (sub, ...) if required else (Optional[sub], None)
    return create_model(name, __config__=ConfigDict(extra="allow"), **defs)


def _has_required(node: dict[str, Any]):
    for v in node.values():
        if isinstance(v, ConfigField):
            yield v.required
        else:
            yield from _has_required(v)


def _default_for(f: ConfigField, root: Any, expand=None) -> Any:
    d = f.default
    if isinstance(d, str):
        d = expand(d) if expand else d.replace("{root}", str(root))
    if d is None:
        d = {"string": "", "path": "", "integer": 0, "number": 0.0, "boolean": False,
             "object": {}, "array": []}[f.type]
    return d


def nest_defaults(fields: list[ConfigField], root: Any, expand=None) -> tuple[dict[str, Any], dict[str, str]]:
    """(valores por defecto anidados, ayuda por campo) a partir de config_template.fields."""
    values: dict[str, Any] = {}
    helps: dict[str, str] = {}
    for f in fields:
        node = values
        parts = f.name.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = _default_for(f, root, expand)
        helps[f.name] = (f.description + ("" if f.required else " (opcional)")).strip()
    return values, helps


def default_config(tool: ToolSpec, root: Any, expand=None) -> Optional[dict[str, Any]]:
    """Conf 'hipotético' de la herramienta con los valores por defecto de su plantilla (None si no declara campos)."""
    if not tool.config_template or not tool.config_template.fields:
        return None
    return nest_defaults(tool.config_template.fields, root, expand)[0]


def build_config_model(tool: ToolSpec) -> Optional[type[BaseModel]]:
    if not tool.config_template or not tool.config_template.fields:
        return None
    return _build(f"Conf_{tool.id}", _group(tool.config_template.fields))


def validate_config(tool: ToolSpec, data: dict[str, Any]) -> list[str]:
    """Lista de problemas ('campo: mensaje'). [] = el conf cumple el esquema declarado."""
    model = build_config_model(tool)
    if model is None:
        return []
    try:
        model.model_validate(data)
        return []
    except ValidationError as e:
        return [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]


def validate_params(tool: ToolSpec, params: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str, str]]]:
    """(parámetros limpios, errores[(nombre, 'missing'|'invalid', mensaje)])."""
    clean: dict[str, Any] = {}
    errors: list[tuple[str, str, str]] = []
    for p in tool.params:
        raw = params.get(p.name, p.default)
        if raw in (None, ""):
            if p.required:
                errors.append((p.name, "missing", f"falta el parámetro '{p.name}'"))
            continue
        try:
            value = TypeAdapter(_PY[p.type]).validate_python(raw)
        except ValidationError as e:
            errors.append((p.name, "invalid", f"'{p.name}': {e.errors()[0]['msg']} (esperado {p.type})"))
            continue
        if p.choices and value not in p.choices:
            errors.append((p.name, "invalid", f"'{p.name}' debe ser uno de {p.choices}"))
            continue
        clean[p.name] = value
    return clean, errors


def config_json_schema(tool: ToolSpec) -> Optional[dict[str, Any]]:
    model = build_config_model(tool)
    return model.model_json_schema() if model else None
