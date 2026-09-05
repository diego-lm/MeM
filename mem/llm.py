"""Adapter de proveedor LLM. Interfaz única: completar(system, mensajes, tools) -> dict.

Mensajes normalizados (formato propio, se convierte al de cada proveedor):
  {"role": "user"|"assistant", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id","name","args"}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

Respuesta normalizada:
  {"texto": str, "tool_calls": [...], "tokens_in": int, "tokens_out": int}

ponytail: llamadas no-streaming; el SSE del API emite eventos por paso, no
token a token. Streaming fino cuando la UX lo pida.
"""
import base64
import json
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path

from . import servicios


def crear(cfg: dict):
    prov = cfg.get("proveedor", "anthropic")
    if prov == "anthropic":
        return _Anthropic(cfg)
    if prov == "claude_code":
        return _ClaudeCode(cfg)
    return _OpenAICompat(cfg)


def hace_tools(proveedor: str) -> bool:
    """¿Ese proveedor llama herramientas por API? El CLI de Claude Code no: usa su
    propio MCP, y por eso no puede atender el taller —no llega a crear_nube ni al
    diálogo que autoriza el gasto—. La app lo mira para no ofrecerlo ahí."""
    return proveedor != "claude_code"


CLAUDE_CODE_MODELOS = ["sonnet", "opus", "haiku"]  # alias estables del CLI, no ids versionados


# Modalidades por id: t=texto, i=imagen, a=audio, v=video. El primer patrón que
# coincide gana, así que lo específico va arriba (omni antes que visión).
# ponytail: /v1/models no publica modalidades — ni OpenAI ni LM Studio — así que
# se infieren del nombre. Si LM Studio expone el dato (/api/v0/models trae
# `vision`), esto se reemplaza por la lectura real en vez de crecer la tabla.
_MODALIDADES = [
    # salida "" = no produce nada legible (embeddings): elegirlo para chatear es
    # un error, y en la lista se ve como tal.
    # patrones anclados con guion: "e5-" suelto pescaba "fabl-e5-composer"
    (("embedding", "embed-", "-embed", "reranker", "bge-", "-e5-"), "t", ""),
    (("omni", "4o-audio", "audio-preview"), "tia", "ta"),
    (("whisper", "stt", "speech-to-text", "parakeet", "canary"), "a", "t"),
    (("voxtral", "audio-flamingo", "qwen2-audio", "qwen3-audio"), "ta", "t"),
    (("tts", "text-to-speech", "orpheus", "kokoro", "bark", "xtts"), "t", "a"),
    (("flux", "krea", "stable-diffusion", "sd3", "sdxl", "dall-e", "qwen-image",
      "hunyuan-image", "imagen-", "-image"), "t", "i"),
    (("sora", "veo-", "ltx-video", "wan2", "-video"), "t", "v"),
    (("-vl", "vl-", "vision", "llava", "gpt-4o", "gpt-5", "gemma-3", "gemma-4",
      "pixtral", "internvl", "minicpm-v", "moondream", "sonnet", "opus", "haiku"), "ti", "t"),
]


def modalidades(modelo: str) -> tuple[str, str]:
    m = str(modelo).lower()
    for patrones, entrada, salida in _MODALIDADES:
        if any(p in m for p in patrones):
            return entrada, salida
    return "t", "t"


def modelos(cfg: dict, timeout: float = 5.0) -> list[dict]:
    """Modelos disponibles para el proveedor de `cfg` (dropdown de Settings),
    cada uno como {id, in, out} con las modalidades que soporta.
    anthropic no lista (API sin endpoint de catálogo relevante aquí) -> []."""
    prov = cfg.get("proveedor", "anthropic")
    if prov == "claude_code":
        ids = CLAUDE_CODE_MODELOS
    elif prov == "openai":
        from openai import OpenAI
        key = os.environ.get(cfg.get("api_key_env") or "", "") or "lm-studio"
        client = OpenAI(base_url=cfg["base_url"], api_key=key, timeout=timeout)
        ids = sorted(m.id for m in client.models.list().data)
    else:
        return []
    return [{"id": i, "in": ent, "out": sal} for i in ids for ent, sal in [modalidades(i)]]


def contexto_max(cfg: dict, timeout: float = 3.0) -> int:
    """Ventana de contexto del modelo EN USO, en tokens. Para Claude es la
    conocida; LM Studio la publica por su API nativa (/api/v0/models) con el
    valor REAL del modelo cargado — sus settings, no el máximo teórico.
    0 = no se sabe (y no se advierte nada)."""
    if cfg.get("proveedor") in ("anthropic", "claude_code"):
        return 200_000
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base:
        return 0
    try:
        import httpx
        r = httpx.get(base.removesuffix("/v1") + "/api/v0/models", timeout=timeout)
        for m in r.json().get("data", []):
            if m.get("id") == cfg.get("modelo"):
                return int(m.get("loaded_context_length") or m.get("max_context_length") or 0)
    except Exception:
        pass
    return 0


DESCRIBIR_PROMPT = ("Describe en español, con detalle, el contenido de esta imagen: qué se ve, "
                    "todo el texto legible transcrito, y de qué trata. La descripción reemplaza "
                    "a la imagen dentro de una memoria de solo texto.")
MAX_DESCRIPCION = 1500   # la descripción ES el contenido guardado: 300 tokens lo truncaban a media frase
# Sin esto el SDK default (10 min Anthropic, 600s openai) cuelga el turno entero
# en silencio si el proveedor tarda en cargar (LM Studio JIT) — visto 2026-08-09:
# una imagen adjunta se quedó "pensando" indefinido porque describir_imagen esperó
# de más a qwen3-vl-8b cargando. 240s: un turno normal entra de sobra (el CLI de
# abajo, que SÍ puede tardar por su propio arranque de proceso, usa TIMEOUT_CLI).
TIMEOUT_API = 240


def describir_imagen(cfg: dict, ruta: Path | list[Path], prompt: str = DESCRIBIR_PROMPT) -> str:
    """Describe una imagen adjunta con el proveedor de `cfg` (visión), para que el
    procesador de inbox pueda categorizarla por su contenido real y no solo el
    nombre de archivo. claude_code (CLI -p) no soporta visión en este wrapper -> "".

    Quién es capaz de verla lo decide `media._con_modelo`; acá se usa cfg["modelo"] tal cual.

    `ruta` puede ser una LISTA: van todas en el mismo mensaje. Es lo que necesita
    un video —los fotogramas se entienden juntos, no de a uno: el modelo ve qué
    cambia entre uno y otro— y de paso son N veces menos viajes al proveedor.
    """
    prov = cfg.get("proveedor", "anthropic")
    if prov == "claude_code":
        return ""
    rutas = [ruta] if isinstance(ruta, Path) else list(ruta)
    imgs = [(base64.b64encode(r.read_bytes()).decode(),
             mimetypes.guess_type(r.name)[0] or "image/jpeg") for r in rutas]
    tope = MAX_DESCRIPCION * len(imgs)
    if prov == "anthropic":
        import anthropic
        key = os.environ.get(cfg.get("api_key_env") or "ANTHROPIC_API_KEY") or None
        r = anthropic.Anthropic(api_key=key, timeout=TIMEOUT_API).messages.create(
            model=cfg["modelo"], max_tokens=tope, messages=[{"role": "user", "content": [
                *[{"type": "image", "source": {"type": "base64", "media_type": mime, "data": datos}}
                  for datos, mime in imgs],
                {"type": "text", "text": prompt},
            ]}])
        return "".join(b.text for b in r.content if b.type == "text")
    from openai import OpenAI
    key = os.environ.get(cfg.get("api_key_env", ""), "") or "lm-studio"
    client = OpenAI(base_url=cfg["base_url"], api_key=key, timeout=TIMEOUT_API)
    r = client.chat.completions.create(model=cfg["modelo"], max_tokens=tope, messages=[{"role": "user", "content": [
        {"type": "text", "text": prompt},
        *[{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{datos}"}}
          for datos, mime in imgs],
    ]}])
    return r.choices[0].message.content or ""


def probar(cfg: dict, timeout: int = 30) -> dict:
    """Prueba de conexión del proveedor configurado (Settings, spec §7).

    Con timeout propio: un modelo local cargando no debe colgar el test.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(crear(cfg).completar, "Responde solo la palabra: ok",
                        [{"role": "user", "content": "ping"}], None)
        r = fut.result(timeout=timeout)
        return {"ok": True, "proveedor": cfg.get("proveedor"), "respuesta": r["texto"].strip()[:80]}
    except FutTimeout:
        return {"ok": False, "proveedor": cfg.get("proveedor"),
                "detalle": f"sin respuesta en {timeout}s (¿modelo cargando o endpoint caído?)"}
    except Exception as e:
        return {"ok": False, "proveedor": cfg.get("proveedor"), "detalle": f"{type(e).__name__}: {e}"}
    finally:
        ex.shutdown(wait=False, cancel_futures=True)  # no esperar un proveedor colgado


class _Anthropic:
    def __init__(self, cfg):
        import anthropic
        key = os.environ.get(cfg.get("api_key_env") or "ANTHROPIC_API_KEY") or None
        self.client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT_API)  # None → el SDK usa ANTHROPIC_API_KEY
        self.modelo = cfg["modelo"]

    def completar(self, system, mensajes, tools=None, forzar_tool: str = ""):
        conv = []
        for m in mensajes:
            if m["role"] == "tool":
                bloque = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if conv and conv[-1]["role"] == "user" and isinstance(conv[-1]["content"], list):
                    conv[-1]["content"].append(bloque)  # resultados consecutivos en un solo mensaje user
                else:
                    conv.append({"role": "user", "content": [bloque]})
            elif m.get("tool_calls"):
                bloques = [{"type": "text", "text": m["content"]}] if m["content"] else []
                bloques += [{"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["args"]}
                            for t in m["tool_calls"]]
                conv.append({"role": "assistant", "content": bloques})
            else:
                conv.append({"role": m["role"], "content": m["content"]})
        kw = {}
        if tools:
            kw["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                           for t in tools]
            if forzar_tool:
                kw["tool_choice"] = {"type": "tool", "name": forzar_tool}
        r = self.client.messages.create(model=self.modelo, system=system, messages=conv, max_tokens=4096, **kw)
        texto = "".join(b.text for b in r.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "args": dict(b.input)} for b in r.content if b.type == "tool_use"]
        return {"texto": texto, "tool_calls": calls,
                "tokens_in": r.usage.input_tokens, "tokens_out": r.usage.output_tokens}


def _motivo_cli(salida: str) -> str:
    """El motivo legible de un error del CLI de Claude.

    El CLI escribe su error como JSON en stdout
    ({"is_error":true,...,"result":"Credit balance is too low"}): mostrarlo crudo
    llena la burbuja de error con 400 caracteres de JSON y esconde la única línea
    que importa (visto 2026-08-05).
    """
    try:
        d = json.loads(salida)
    except (ValueError, TypeError):
        return ""
    return str(d.get("result") or d.get("error") or "").strip()[:300] if isinstance(d, dict) else ""


# El CLI de Claude Code trae su PROPIA conexión MCP al servidor `mem` de esta
# misma app (ver mem/mcp.py — la memoria compartida con Desktop/Code), aparte de
# —e invisible para— los tools que arma chat.py. En headless (-p, sin TTY) toda
# llamada a un tool que necesite aprobación se deniega en silencio: el modelo
# quería mcp__mem__leer_pagina para sacar la ruta del adjunto de una imagen y
# se topó con eso (visto 2026-08-06: "necesito tu permiso para leer esa página").
#
# Ojo: acá NO sirve de nada lo que diga media.md ni el system prompt. La capa de
# permisos del CLI corta antes, y por eso el 2026-08-06 seguía saliendo "necesito
# tu permiso para lanzar la generación con ComfyUI (mcp__mem__crear_imagen)" con
# el prompt ya arreglado: crear_imagen simplemente no estaba en esta tupla.
#
# Se preautoriza todo lo que LEE y todo lo que genera EN LOCAL (ComfyUI es la
# máquina de Diego: gratis y sin efectos fuera de la base).
# Quedan fuera a propósito:
#   · crear_nube — gasta créditos, y por este camino el diálogo de confirmación de
#     la app NO corre (el CLI llama al MCP directo, sin pasar por chat._ejecutar),
#     así que preautorizarla sería gastar plata sin preguntar.
#   · guardar_entrada/capturar/editar_memoria — escriben la memoria.
#   · procesar_inbox — dispara el pipeline nocturno entero.
_MCP_MEM_OK = ("buscar", "leer_pagina", "conexiones", "grep", "buscar_memorias",
               "arbol_subjects", "crear_imagen", "crear_video", "guardar_creacion",
               "modelos_nube", "arrancar_servicio")


TIMEOUT_CLI = 300   # ponytail: un turno de chat entra de sobra; generar NO va por acá


class _ClaudeCode:
    """Claude Code instalado localmente (CLI `claude -p`): usa la suscripción, sin API key.

    No expone tool-calling → el motor cae automáticamente a retrieval scripted.
    """
    tools_ok = False
    # ...pero SÍ puede crear medios: el CLI trae su propia conexión MCP al server
    # `mem` (con crear_imagen y compañía) en cualquier modo, aunque el modo no
    # declare la herramienta `crear`. chat.py lo mira para vigilar los pedidos de
    # permiso que ese camino puede producir.
    mcp_propio = True
    sesion = proyecto = ""      # los pone chat.responder; viajan al MCP por env (abajo)

    def __init__(self, cfg):
        self.exe = shutil.which("claude")
        if not self.exe:
            raise RuntimeError("no se encontró el comando 'claude' (¿Claude Code instalado?)")
        self.modelo = str(cfg.get("modelo") or "")
        self.hamuq = str(cfg.get("hamuq") or "")

    def completar(self, system, mensajes, tools=None, forzar_tool: str = ""):
        dialogo = "\n\n".join(f"[{m['role']}]\n{m['content']}"
                              for m in mensajes if m["role"] in ("user", "assistant") and m.get("content"))
        cmd = [self.exe, "-p", "--output-format", "json", "--append-system-prompt", system]
        if self.modelo:
            cmd += ["--model", self.modelo]
        if self.hamuq:
            # el proceso corre con cwd del repo (mem_tray.py), fuera de la base de
            # Diego: sin esto, el propio Read del CLI se negaba a abrir cualquier
            # adjunto de la base ("necesito tu aprobación...") — no había nada que
            # aprobar, era un directorio no permitido en una llamada sin TTY.
            cmd += ["--add-dir", self.hamuq]
        cmd += ["--allowedTools", " ".join(f"mcp__mem__{n}" for n in _MCP_MEM_OK)]
        # El CLI lanza `python -m mem.mcp` como hijo y le hereda el entorno: es el
        # único canal por el que ese MCP puede enterarse de en qué sesión de la app
        # está corriendo. Sin esto, lo que genere queda sin `sesion` ni `proyecto`
        # en el frontmatter y la app no lo linkea a la conversación (visto
        # 2026-08-06). Env del subprocess, no os.environ: dos turnos en paralelo no
        # se pisan.
        env = {**os.environ, **{k: v for k, v in (("MEM_SESION", self.sesion),
                                                  ("MEM_PROYECTO", self.proyecto)) if v}}
        try:
            r = subprocess.run(cmd, input=dialogo, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=TIMEOUT_CLI, env=env)
        except subprocess.TimeoutExpired:
            # el str() de TimeoutExpired ES el argv entero, y ahí adentro viaja el
            # system prompt completo: sin este except, un turno lento llegaba al
            # chat como 2.000 caracteres de línea de comandos (visto 2026-08-07)
            raise RuntimeError(
                f"claude CLI: no respondió en {TIMEOUT_CLI // 60} min. Una generación larga "
                "no entra por acá: pedila en el taller (modo media), que usa el agente de «crear»."
            ) from None
        if r.returncode != 0:
            raise RuntimeError(f"claude CLI: {_motivo_cli(r.stdout) or (r.stderr or r.stdout).strip()[:300]}")
        data = json.loads(r.stdout)
        # sale con 0 igual: el error viene marcado dentro del JSON
        if data.get("is_error"):
            raise RuntimeError(f"claude CLI: {_motivo_cli(r.stdout) or 'error sin detalle'}")
        uso = data.get("usage") or {}
        return {"texto": data.get("result", ""), "tool_calls": [],
                "tokens_in": int(uso.get("input_tokens", 0) or 0),
                "tokens_out": int(uso.get("output_tokens", 0) or 0)}


class _OpenAICompat:
    """LM Studio (http://localhost:1234/v1) o cualquier endpoint OpenAI-compatible."""

    def __init__(self, cfg):
        from openai import OpenAI
        key = os.environ.get(cfg.get("api_key_env", ""), "") or "lm-studio"
        self.client = OpenAI(base_url=cfg["base_url"], api_key=key, timeout=TIMEOUT_API)
        self.modelo = cfg["modelo"]

    def completar(self, system, mensajes, tools=None, forzar_tool: str = ""):
        conv = [{"role": "system", "content": system}]
        for m in mensajes:
            if m["role"] == "tool":
                conv.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            elif m.get("tool_calls"):
                conv.append({"role": "assistant", "content": m["content"] or None,
                             "tool_calls": [{"id": t["id"], "type": "function",
                                             "function": {"name": t["name"],
                                                          "arguments": json.dumps(t["args"], ensure_ascii=False)}}
                                            for t in m["tool_calls"]]})
            else:
                conv.append({"role": m["role"], "content": m["content"]})
        kw = {}
        if tools:
            kw["tools"] = [{"type": "function",
                            "function": {"name": t["name"], "description": t["description"],
                                         "parameters": t["parameters"]}} for t in tools]
            if forzar_tool:
                # "required" (llamá a ALGUNA tool), no el objeto {"function": {...}}:
                # LM Studio solo acepta none/auto/required y con el objeto tira
                # 400 Invalid tool_choice type (medido 2026-08-06 contra qwen3-vl-8b).
                # Alcanza igual: lo que se quiere es cortar la excusa en prosa.
                kw["tool_choice"] = "required"
        # cualquier llamada local (chat, triaje, resumen) cuenta como uso: es lo
        # que evita que el vigilante de ocioso le suelte el modelo por debajo
        servicios.marcar_uso()
        r = self.client.chat.completions.create(model=self.modelo, messages=conv, **kw)
        msg = r.choices[0].message
        calls = [{"id": t.id, "name": t.function.name, "args": json.loads(t.function.arguments or "{}")}
                 for t in (msg.tool_calls or [])]
        uso = r.usage
        return {"texto": msg.content or "", "tool_calls": calls,
                "tokens_in": getattr(uso, "prompt_tokens", 0) or 0,
                "tokens_out": getattr(uso, "completion_tokens", 0) or 0}
