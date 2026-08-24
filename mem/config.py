import os
import re
import tomllib
from pathlib import Path

DEFAULTS = {
    "hamuq": r"W:\Dropbox\MeM",
    "proveedor": "anthropic",
    "modelo": "claude-sonnet-5",
    "base_url": "http://localhost:1234/v1",
    "api_key_env": "ANTHROPIC_API_KEY",
    "puerto": 8765,
    "token": "",
    # llave de POST /mcp/{secreto} (Cowork, claude.ai vía Funnel). Aparte del
    # token Bearer: la PWA no manda headers y no hay que romperla. Vacío = ruta apagada.
    "mcp_secreto": "",
    # URL pública de la PWA (el Funnel). Las tools MCP la usan para devolver un
    # link que abre la memoria en el browser cuando el cliente no puede mostrarla.
    "app_url": "",
    "comfy_url": "http://127.0.0.1:8188",   # ComfyUI local (modo media)
    "comfy_cmd": r"W:\ComfyUI\arrancar.bat",  # cómo arrancarlo (servicios.py)
    "comfy_workflow": "imagen",             # workflow por defecto de crear_imagen
    # Media: backend preferido por tipo (comfyui | higgsfield | preguntar) y el
    # job_type de Higgsfield por defecto para cada uno. Van al prompt del modo
    # media (crear.preferencias): guían al agente, no lo encierran.
    "media_imagen": "comfyui",
    "media_video": "higgsfield",
    "media_audio": "higgsfield",
    "higgs_imagen": "",
    "higgs_video": "",
    "higgs_audio": "",
    # Minutos sin usar un modelo local antes de soltarle la VRAM (servicios.py).
    # 0 = nunca. LM Studio retiene ~12 de 16 GB aunque esté ocioso: sin esto, la
    # primera generación después de un rato de chat se come el OOM.
    "idle_minutos": 15,
    # Modelo de embeddings para la búsqueda semántica (indice.py). ONNX local en
    # CPU; se descarga una sola vez al cache de Hugging Face con `mem index`.
    # Xenova = mismo peso que intfloat/multilingual-e5-small con int8 portable.
    "embed_modelo": "Xenova/multilingual-e5-small",
    # Hasta 3 combinaciones de estilo guardadas (pedido 2026-08-09), cada una
    # {nombre, palette, themePref, burbuja, radio, borde, fuente} — viajan por
    # acá (y no localStorage) para valer en todos los dispositivos de Diego. La
    # SELECCIÓN activa sigue siendo per-device (state.js); esto es solo el catálogo.
    "estilos_guardados": [],
}

# los únicos que PATCH /config puede tocar
CAMPOS_EDITABLES = ("proveedor", "modelo", "base_url", "hamuq", "comfy_url", "comfy_cmd", "comfy_workflow",
                    "media_imagen", "media_video", "media_audio", "higgs_imagen", "higgs_video", "higgs_audio",
                    "idle_minutos", "embed_modelo", "estilos_guardados")


def ruta_toml(path: str | None = None) -> Path:
    return Path(path or os.environ.get("MEM_CONFIG", "") or Path(__file__).resolve().parent.parent / "config.toml")


def cargar(path: str | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = ruta_toml(path)
    if p.exists():
        cfg.update(tomllib.loads(p.read_text(encoding="utf-8")))
    cfg["hamuq"] = Path(cfg["hamuq"])
    return cfg


def _literal(valor) -> str:
    """El valor tal como se escribe en TOML. Un número va sin comillas: guardarlo
    como texto lo devolvía como str en el próximo arranque, y ahí el ajuste que
    entró como int volvía distinto de como se guardó.
    Lista/dict → array/tabla inline TOML, todo en la misma línea: estilos_guardados
    es una lista de dicts y actualizar() reemplaza por regex de línea completa
    (^clave\\s*=.*$) — un value con salto de línea le rompería el reemplazo."""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in valor) + "]"
    if isinstance(valor, dict):
        return "{" + ", ".join(f"{k} = {_literal(v)}" for k, v in valor.items()) + "}"
    s = str(valor)
    if "\\" in s and "'" not in s:   # rutas Windows → string literal TOML (sin escapes)
        return f"'{s}'"
    # básico con escapes de verdad: un nombre de estilo con comillas ("B \"rara\"")
    # con el criterio viejo (comilla simple si no hay backslash) rompía el TOML —
    # bug real, visto al probar el round-trip de estilos_guardados (2026-08-09).
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def actualizar(cfg: dict, cambios: dict, path: str | None = None) -> dict:
    """PATCH /config: reescribe solo las claves editables en config.toml (regex,
    preserva comentarios y el resto del archivo) y actualiza `cfg` en memoria."""
    cambios = {k: v for k, v in cambios.items() if k in CAMPOS_EDITABLES and v is not None}
    if not cambios:
        return cfg
    p = ruta_toml(path)
    texto = p.read_text(encoding="utf-8") if p.exists() else ""
    for clave, valor in cambios.items():
        linea = f"{clave} = {_literal(valor)}"
        rx = re.compile(rf'^{clave}\s*=.*$', re.M)
        texto = rx.sub(lambda _: linea, texto) if rx.search(texto) else texto + f"\n{linea}\n"
    p.write_text(texto, encoding="utf-8")
    cfg.update(cambios)
    if "hamuq" in cambios:
        cfg["hamuq"] = Path(cambios["hamuq"])
    return cfg


def sanitizado(cfg: dict) -> dict:
    """Vista segura para el API: nunca expone el valor de la API key ni el token crudo."""
    token = str(cfg.get("token") or "")
    return {
        "proveedor": cfg.get("proveedor"), "modelo": cfg.get("modelo"), "base_url": cfg.get("base_url"),
        "api_key_env": cfg.get("api_key_env"), "hamuq": str(cfg.get("hamuq")), "puerto": cfg.get("puerto"),
        "comfy_url": cfg.get("comfy_url"), "comfy_cmd": cfg.get("comfy_cmd"),
        "comfy_workflow": cfg.get("comfy_workflow"),
        **{k: cfg.get(k) for k in ("media_imagen", "media_video", "media_audio",
                                   "higgs_imagen", "higgs_video", "higgs_audio", "idle_minutos",
                                   "embed_modelo", "estilos_guardados")},
        "token": f"•••• {token[-4:]}" if token else "",
    }
