"""Agentes de IA: cada uno con nombre, icono, proveedor/modelo y system prompt
propios. Persisten en agentes.json junto a config.toml. Cada tarea que usa LLM
(chat, procesar, resumir, crear) se asigna a un agente; sin asignación (o si
el agente asignado ya no existe) se usa el primero de la lista.

Dos agentes base (Smart, Cheap) siempre existen y nunca se pueden borrar —
`guardar` rechaza cualquier lista que no los incluya, y `cargar` los reinyecta
si un agentes.json viejo (o editado a mano) los perdió.
"""
import json

from . import config

TAREAS = ("chat", "procesar", "resumir", "crear")
CAMPOS_PROV = ("proveedor", "modelo", "base_url", "api_key_env")

BASE = (
    {"id": "smart", "nombre": "Smart", "icono": "✦", "system_prompt": "",
     "proveedor": "anthropic", "modelo": "claude-sonnet-5", "base_url": "", "api_key_env": "ANTHROPIC_API_KEY"},
    {"id": "cheap", "nombre": "Cheap", "icono": "⚡", "system_prompt": "",
     "proveedor": "anthropic", "modelo": "claude-haiku-4-5-20251001", "base_url": "", "api_key_env": "ANTHROPIC_API_KEY"},
)
BASE_IDS = tuple(a["id"] for a in BASE)


def _ruta(path: str | None = None):
    return config.ruta_toml(path).with_name("agentes.json")


def _defecto(cfg: dict) -> dict:
    return {"agentes": [dict(a) for a in BASE], "asignaciones": {}}


def _con_base(data: dict) -> dict:
    presentes = {a.get("id") for a in data["agentes"]}
    faltan = [dict(a) for a in BASE if a["id"] not in presentes]
    if faltan:
        data["agentes"] = data["agentes"] + faltan
    return data


def cargar(cfg: dict, path: str | None = None) -> dict:
    p = _ruta(path)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("agentes"):
            data.setdefault("asignaciones", {})
            return _con_base(data)
    return _defecto(cfg)


def guardar(data: dict, path: str | None = None) -> dict:
    ids = {a.get("id") for a in data.get("agentes", [])}
    faltan = [i for i in BASE_IDS if i not in ids]
    if faltan:
        raise ValueError(f"los agentes base no se pueden borrar: {', '.join(faltan)}")
    _ruta(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def para(cfg: dict, tarea: str, agente_id: str | None = None, path: str | None = None) -> dict:
    """cfg fusionado con el agente asignado a `tarea` (o el de `agente_id`),
    listo para llm.crear. Incluye el 'system_prompt' del agente."""
    data = cargar(cfg, path)
    aid = agente_id or data["asignaciones"].get(tarea)
    ags = data["agentes"]
    ag = next((a for a in ags if a.get("id") == aid), ags[0])
    return {**cfg, **{k: ag.get(k, "") for k in CAMPOS_PROV},
            "system_prompt": str(ag.get("system_prompt") or "")}
