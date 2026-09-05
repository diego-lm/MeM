"""Servidor MCP de MeM: expone la memoria (y la generación) a los clientes de
Claude — Desktop y Code por stdio, Cowork y claude.ai por POST /mcp del API.

Espejo del cliente mcps.py: JSON-RPC 2.0 en líneas NDJSON. stdout es SOLO
protocolo; diagnóstico a stderr (Desktop lo guarda en sus logs de MCP).
`python -m mem.mcp` corre el servidor; `python -m mem.mcp demo` el autocheck.
"""
import base64
import json
import os
import re
import sys
from pathlib import Path

from . import config, crear, memoria

PROTOCOLO = "2024-11-05"    # fallback; a un server solo-tools todas le dan igual
MAX_LISTA = 30              # buscar_memorias: tope de fichas por respuesta
MAX_IMG = 2_000_000         # imágenes generadas: no incrustar más de ~2 MB
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif"}

# Estos 5 leen memoria: sin `proyecto` solo ven lo público (Todo). Con `proyecto`
# de uno privado hace falta además `clave` (la que se genera en Gestionar) —
# salvo que MEM_CLAVES ya la traiga puesta (stdio, Desktop/Code).
_PROY_CLAVE = {
    "proyecto": {"type": "string", "description": "ver solo lo público (default) o además este proyecto"},
    "clave": {"type": "string", "description": "clave del proyecto, si es privado y no viene por MEM_CLAVES"}}

_MEM = [
    {"name": "buscar",
     "description": ("Busca términos en los índices de la memoria personal de Diego (app MeM: "
                     "base de conocimiento en Markdown con sus notas, entradas y capturas). "
                     "Devuelve líneas candidatas con su archivo. Usar SIEMPRE antes de leer páginas."),
     "parameters": {"type": "object", "properties": {"consulta": {"type": "string"}, **_PROY_CLAVE},
                    "required": ["consulta"]}},
    {"name": "leer_pagina",
     "description": ("Lee una página de la memoria por su ruta relativa (ej. "
                     "06_Biblioteca_Conocimiento/Entradas/x.md). Las páginas largas vienen en "
                     "partes: el final avisa y se sigue con parte=2, 3…"),
     "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                    "parte": {"type": "integer", "description": "para páginas largas; default 1"},
                    **_PROY_CLAVE},
                    "required": ["path"]}},
    {"name": "conexiones",
     "description": ("Conexiones de una entrada de la memoria (slug de buscar_memorias o de la ruta "
                     "Entradas/<slug>.md): sesiones que la citaron, entradas del mismo subject, "
                     "[[wikilinks]] en ambos sentidos y relacionadas por similitud semántica (con "
                     "score). Úsala después de buscar/buscar_memorias/leer_pagina para navegar el "
                     "vecindario de una memoria y traer contexto que la búsqueda no encontró."),
     "parameters": {"type": "object", "properties": {"slug": {"type": "string"}, **_PROY_CLAVE},
                    "required": ["slug"]}},
    {"name": "grep",
     "description": "Busca un patrón (texto o regex) en todos los .md de la memoria. Fallback cuando los índices no dan resultado.",
     "parameters": {"type": "object", "properties": {"patron": {"type": "string"}, **_PROY_CLAVE},
                    "required": ["patron"]}},
    {"name": "buscar_memorias",
     "description": ("Búsqueda estructurada sobre las entradas de la memoria: texto libre y/o filtros "
                     "por tag, subject (prefijo: 'Tecnologia/IA' incluye subniveles), fechas YYYY-MM-DD "
                     "y lugar. Devuelve fichas JSON (slug, titulo, fecha, subjects, resumen). Ideal para "
                     "'qué guardé en julio sobre X'."),
     "parameters": {"type": "object", "properties": {
         "texto": {"type": "string"}, "tag": {"type": "string"}, "subject": {"type": "string"},
         "desde": {"type": "string", "description": "YYYY-MM-DD"},
         "hasta": {"type": "string", "description": "YYYY-MM-DD"},
         "lugar": {"type": "string"}, **_PROY_CLAVE}}},
    {"name": "arbol_subjects",
     "description": ("Árbol de categorías (subjects) de la memoria con conteo de entradas. Consúltalo "
                     "antes de guardar_entrada para usar subjects existentes en vez de inventar nuevos."),
     "parameters": {"type": "object", "properties": {}}},
    {"name": "guardar_entrada",
     "description": ("Guarda una síntesis valiosa y curada como entrada de la memoria de Diego (o agrega "
                     "una actualización fechada si ya existe una con ese título). subjects = temas del "
                     "árbol (ver arbol_subjects); tags = etiquetas libres. Para notas rápidas sin curar "
                     "usa capturar."),
     "parameters": {"type": "object", "properties": {
         "titulo": {"type": "string"}, "contenido": {"type": "string"},
         "subjects": {"type": "array", "items": {"type": "string"}},
         "tags": {"type": "array", "items": {"type": "string"}}},
         "required": ["titulo", "contenido", "subjects"]}},
    {"name": "capturar",
     "description": ("Deja una nota, idea o material en el inbox de la memoria para que MeM lo procese "
                     "después (título, tags y categorías los pone su procesador). Cero fricción: no pidas "
                     "subjects ni formato. Úsala cuando Diego diga 'anota esto', 'guárdame esto para después'."),
     "parameters": {"type": "object", "properties": {
         "contenido": {"type": "string"},
         "contexto": {"type": "string", "description": "contexto extra de Diego sobre la captura"},
         "tags": {"type": "array", "items": {"type": "string"}}},
         "required": ["contenido"]}},
    {"name": "editar_memoria",
     "description": ("Edita una entrada existente de la memoria (slug de buscar_memorias o de la ruta "
                     "Entradas/<slug>.md). Cambiar título, resumen o dejar una nota guarda la versión "
                     "anterior completa: nada se pierde y Diego puede verla en el visor de MeM. "
                     "tags y subjects REEMPLAZAN la lista entera, así que manda la lista final."),
     "parameters": {"type": "object", "properties": {
         "slug": {"type": "string"},
         "titulo": {"type": "string"},
         "resumen": {"type": "string", "description": "reemplaza la sección '## Resumen'"},
         "nota": {"type": "string", "description": "queda fechada en el registro histórico"},
         "tags": {"type": "array", "items": {"type": "string"}},
         "subjects": {"type": "array", "items": {"type": "string"}}},
         "required": ["slug"]}},
    {"name": "procesar_inbox",
     "description": ("Procesa las capturas pendientes del inbox de MeM con su LLM configurado y las "
                     "convierte en entradas catalogadas. Puede tardar minutos si hay varias. Llamarla "
                     "solo si Diego lo pide."),
     "parameters": {"type": "object", "properties": {}}},
]


def _tool(t: dict) -> dict:
    return {"name": t["name"], "description": t["description"],
            "inputSchema": t.get("parameters") or {"type": "object", "properties": {}}}


TOOLS = [_tool(t) for t in _MEM + crear.TOOLS]
NOMBRES = tuple(t["name"] for t in TOOLS)


RX_ATTACH = re.compile(r"\]\(/attach/([^)]+)\)")


def _imagen(root: Path, salida: str) -> list[dict]:
    """Si la salida referencia una imagen (/attach/...: generada o adjunta a una
    captura), la manda como bloque MCP `image` — Claude Desktop/claude.ai la
    muestran inline. Lo que no cabe o no es imagen se queda con su link."""
    m = RX_ATTACH.search(salida)
    p = root / m.group(1) if m else Path()
    mime = MIME.get(p.suffix.lower())
    if not mime or not p.is_file() or p.stat().st_size > MAX_IMG:
        return []
    return [{"type": "image", "data": base64.b64encode(p.read_bytes()).decode(), "mimeType": mime}]


def _visor(root: Path, ruta: str) -> str:
    """Destino que abre el adjunto EN la app: la ficha de la memoria que lo guarda
    (ahí el visor ya lo muestra con su contexto). Sin ficha todavía —una captura
    sin procesar—, el fichero directo."""
    slug = memoria.entrada_de_adjunto(root, ruta)
    return f"#entry/{slug}" if slug else f"attach/{ruta}"


def _url(cfg: dict, destino: str) -> str:
    """Link que abre la PWA en `#entry/<slug>` o el fichero en `attach/<ruta>`.
    Lo que el cliente de Claude no puede mostrar (un video, un pdf, una foto
    grande) se abre ahí en un toque."""
    base = str(cfg.get("app_url") or "").rstrip("/")
    return f"\n\nAbrir en el browser: {base}/{destino}" if base and destino else ""


def _desde(root: Path, proyecto: str, clave: str, claves: dict) -> str | None:
    """Resuelve y valida el `proyecto` que pidió la tool. Sin proyecto -> None
    (solo lo público). Uno privado exige `clave` válida (la del llamado, o si
    no la trae, la de MEM_CLAVES) — si no, ValueError con mensaje para el modelo."""
    proyecto = str(proyecto or "").strip()
    if not proyecto:
        return None
    p = memoria.proyecto_por_nombre(root, proyecto)
    if not p:
        raise ValueError(f"proyecto desconocido: {proyecto}")
    clave = str(clave or "") or claves.get(p["nombre"], "")
    if not memoria.clave_valida(root, p["nombre"], clave):
        raise ValueError(f"proyecto privado: hace falta la clave de {p['nombre']}")
    return p["nombre"]


def _llamar(cfg: dict, nombre: str, args: dict, claves: dict) -> list[dict]:
    """Ejecuta la tool y devuelve los bloques `content` de MCP."""
    root = cfg["hamuq"]
    if nombre in ("buscar", "leer_pagina", "conexiones", "grep", "buscar_memorias"):
        desde = _desde(root, str(args.get("proyecto") or ""), str(args.get("clave") or ""), claves)
    if nombre == "buscar":
        texto = memoria.buscar(root, str(args.get("consulta") or ""), proyecto=desde)
    elif nombre == "leer_pagina":
        path = str(args.get("path") or "")
        texto = memoria.leer_pagina(root, path, int(args.get("parte") or 1), proyecto=desde)
        adj = RX_ATTACH.search(texto)                                    # foto, video, pdf
        ent = re.search(r"Entradas/([^/\\]+)\.md$", path.replace("\\", "/"))
        texto += _url(cfg, _visor(root, adj.group(1)) if adj else
                           f"#entry/{ent.group(1)}" if ent else "")
    elif nombre == "conexiones":
        slug = str(args.get("slug") or "")
        texto = json.dumps(memoria.conexiones(root, slug, proyecto=desde), ensure_ascii=False)
        texto += _url(cfg, f"#entry/{slug}")
    elif nombre == "grep":
        texto = memoria.grep(root, str(args.get("patron") or ""), proyecto=desde)
    elif nombre == "buscar_memorias":
        res = memoria.buscar_memorias(root, proyecto=desde,
                                      **{k: str(args.get(k) or "")
                                         for k in ("texto", "tag", "subject", "desde", "hasta", "lugar")})
        vista = [{k: e[k] for k in ("slug", "titulo", "fecha", "cuando", "lugar",
                                    "subjects", "tags", "adjunto", "resumen")} for e in res[:MAX_LISTA]]
        nota = f"\n({len(res)} resultados; muestro {MAX_LISTA}, afina los filtros)" if len(res) > MAX_LISTA else ""
        base = str(cfg.get("app_url") or "").rstrip("/")
        nota += f"\nCada ficha se abre en el browser: {base}/#entry/<slug>" if base and vista else ""
        texto = json.dumps(vista, ensure_ascii=False) + nota
    elif nombre == "arbol_subjects":
        arbol = memoria.arbol_subjects(root)
        for g in arbol:
            if g["nombre"] == memoria.GRUPO_PROYECTOS:
                g["hijos"] = [h for h in g["hijos"]
                             if not (p := memoria.proyecto_por_nombre(root, h["nombre"])) or not p["privado"]]
        texto = json.dumps(arbol, ensure_ascii=False)
    elif nombre == "guardar_entrada":
        texto = memoria.guardar_entrada(root, str(args.get("titulo") or ""), str(args.get("contenido") or ""),
                                        [str(s) for s in (args.get("subjects") or [])], origen="claude",
                                        tags=[str(t) for t in (args.get("tags") or [])])
    elif nombre == "editar_memoria":
        slug = str(args.get("slug") or "")
        listas = {k: [str(x) for x in args[k]] if args.get(k) is not None else None
                  for k in ("tags", "subjects")}
        texto = memoria.editar_entrada(root, slug, str(args.get("titulo") or "") or None,
                                       listas["tags"], listas["subjects"],
                                       str(args.get("nota") or ""),
                                       resumen=str(args.get("resumen") or "") or None)
        texto += _url(cfg, f"#entry/{slug}")
    elif nombre == "capturar":
        texto = "capturado: " + memoria.capturar(root, str(args.get("contenido") or ""),
                                                 contexto=str(args.get("contexto") or ""),
                                                 tags=[str(t) for t in (args.get("tags") or [])], origen="claude")
    elif nombre == "procesar_inbox":
        from . import procesar
        texto = json.dumps(procesar.procesar_inbox(cfg), ensure_ascii=False)
    else:                                       # crear.NOMBRES (validado en despachar)
        # Cuando quien llama es el CLI de Claude respondiendo un turno de la app,
        # llega con la sesión en el entorno (llm.py) y lo generado queda linkeado a
        # ella. Desde Claude Desktop o claude.ai no hay sesión: vacío, correcto.
        texto = crear.ejecutar(cfg, nombre, args, proyecto=os.environ.get("MEM_PROYECTO", ""),
                               sesion=os.environ.get("MEM_SESION", ""))
    return [{"type": "text", "text": texto}, *_imagen(root, texto)]


def _error(mid, codigo: int, mensaje: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": codigo, "message": mensaje}}


def despachar(msg: dict, cfg: dict, claves: dict | None = None) -> dict | None:
    """Un mensaje JSON-RPC → su respuesta (None = notificación, no se responde).
    Puro: lo comparten el loop stdio de main() y POST /mcp/{secreto} del API.

    `claves` = {proyecto: clave} para no repetirla en cada llamada — la manda
    main() desde MEM_CLAVES (stdio, Desktop/Code); el endpoint HTTP no manda
    nada, ahí la clave va siempre en los argumentos de la tool."""
    mid, metodo = msg.get("id"), str(msg.get("method") or "")
    if mid is None:
        return None
    params = msg.get("params") or {}
    if metodo == "initialize":
        # eco de la versión del cliente: 2024-11-05…2025-06-18 son idénticas para solo-tools
        r = {"protocolVersion": str(params.get("protocolVersion") or PROTOCOLO),
             "capabilities": {"tools": {}}, "serverInfo": {"name": "mem", "version": "1"}}
    elif metodo == "ping":
        r = {}
    elif metodo == "tools/list":
        r = {"tools": TOOLS}
    elif metodo == "tools/call":
        nombre = str(params.get("name") or "")
        if nombre not in NOMBRES:
            return _error(mid, -32602, f"herramienta desconocida: {nombre}")
        try:
            r = {"content": _llamar(cfg, nombre, params.get("arguments") or {}, claves or {})}
        except Exception as e:      # error de la tool: el modelo lo ve y se corrige
            r = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    else:
        return _error(mid, -32601, f"método no soportado: {metodo}")
    return {"jsonrpc": "2.0", "id": mid, "result": r}


def _claves_env() -> dict:
    """MEM_CLAVES="Obra=xxx;Diario=yyy" del entorno: para stdio (Desktop/Code),
    así no hay que pasar la clave en cada llamada a una tool."""
    crudo = os.environ.get("MEM_CLAVES", "")
    pares = (p.split("=", 1) for p in crudo.split(";") if "=" in p)
    return {k.strip(): v.strip() for k, v in pares if k.strip()}


def main() -> None:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")   # cp1252 rompe al primer acento
    cfg = config.cargar()
    claves = _claves_env()
    for linea in sys.stdin:
        if not linea.strip():
            continue
        try:
            resp = despachar(json.loads(linea), cfg, claves)
        except Exception as e:                  # JSON roto o bug: no matar el server
            print(f"mem.mcp: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if resp is None:
            continue
        try:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError):      # el cliente se fue
            return


def demo():
    """Protocolo directo + e2e real: mcps.Servidor (el cliente que ya vive en el
    repo) contra este server por subprocess, sobre un vault temporal."""
    import tempfile

    from . import mcps
    tmp = Path(tempfile.mkdtemp(prefix="mem_mcp_"))
    (tmp / "vault").mkdir()
    toml = tmp / "config.toml"
    toml.write_text(f"hamuq = '{tmp / 'vault'}'\napp_url = 'https://mem.example'\n", encoding="utf-8")
    cfg = config.cargar(str(toml))

    # despachar puro
    assert despachar({"jsonrpc": "2.0", "method": "notifications/initialized"}, cfg) is None
    assert despachar({"jsonrpc": "2.0", "id": 1, "method": "ping"}, cfg)["result"] == {}
    ini = despachar({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18"}}, cfg)["result"]
    assert ini["protocolVersion"] == "2025-06-18" and "tools" in ini["capabilities"]
    assert despachar({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, cfg)["error"]["code"] == -32601
    assert despachar({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                      "params": {"name": "fantasma"}}, cfg)["error"]["code"] == -32602
    real = memoria.buscar
    memoria.buscar = lambda *a, **k: 1 / 0
    r = despachar({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                   "params": {"name": "buscar", "arguments": {"consulta": "x"}}}, cfg)["result"]
    assert r["isError"] and "ZeroDivisionError" in r["content"][0]["text"]
    memoria.buscar = real

    # e2e por el protocolo de verdad
    srv = mcps.Servidor("mem", {"command": sys.executable, "args": ["-m", "mem.mcp"],
                                "env": {"MEM_CONFIG": str(toml), "PYTHONUTF8": "1"}})
    try:
        nombres = [t["name"] for t in srv.tools]
        assert len(nombres) == len(NOMBRES) and "buscar" in nombres and "crear_imagen" in nombres, nombres
        r = srv.pedir("tools/call", {"name": "capturar", "arguments": {"contenido": "Idea con acentós: café"}})
        assert "capturado: 07_Inbox/" in r["content"][0]["text"]
        cap = next((tmp / "vault" / "07_Inbox").glob("*.md"))
        assert "café" in cap.read_text(encoding="utf-8")            # encoding e2e
        srv.pedir("tools/call", {"name": "guardar_entrada", "arguments": {
            "titulo": "Prueba MCP", "contenido": "Nota vía MCP.", "subjects": ["Pruebas/MCP"]}})
        r = srv.pedir("tools/call", {"name": "buscar_memorias", "arguments": {"texto": "prueba"}})
        assert json.loads(r["content"][0]["text"].split("\n")[0])[0]["titulo"] == "Prueba MCP"
        r = srv.pedir("tools/call", {"name": "editar_memoria", "arguments": {
            "slug": "prueba-mcp", "resumen": "Nota vía MCP, corregida."}})
        assert "editada" in r["content"][0]["text"]
        assert "https://mem.example/#entry/prueba-mcp" in r["content"][0]["text"]   # el link del browser
        v001 = next((tmp / "vault" / memoria.VERSIONES).glob("*/v001.md"))
        assert "Nota vía MCP." in v001.read_text(encoding="utf-8")                  # la versión anterior entera
        r = srv.pedir("tools/call", {"name": "leer_pagina", "arguments": {"path": "../fuera"}})
        assert "fuera de la base" in r["content"][0]["text"]

        # un adjunto no es texto: nunca bytes crudos. Imagen → bloque `image` inline;
        # lo demás (video, pdf) → solo el link para abrirlo en el browser.
        adj = tmp / "vault/07_Inbox/_adjuntos"
        adj.mkdir(parents=True)
        (adj / "foto.png").write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        (adj / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\xff\xfe")
        memoria.guardar_entrada(tmp / "vault", "Con foto", "La foto de la prueba.", ["Pruebas"],
                                adjunto="07_Inbox/_adjuntos/foto.png")
        # el link abre la app en la ficha que guarda el adjunto (su visor lo muestra
        # en contexto); sin ficha todavía, el fichero directo
        for nombre, destino, bloques in (("foto.png", "#entry/con-foto", 2),
                                         ("clip.mp4", "attach/07_Inbox/_adjuntos/clip.mp4", 1)):
            r = srv.pedir("tools/call", {"name": "leer_pagina",
                                         "arguments": {"path": f"07_Inbox/_adjuntos/{nombre}"}})
            texto = r["content"][0]["text"]
            assert "no es texto" in texto and f"https://mem.example/{destino}" in texto, texto
            assert len(r["content"]) == bloques, r["content"][1:]
    finally:
        srv.cerrar()
    print("mcp ok")


if __name__ == "__main__":
    demo() if sys.argv[1:] == ["demo"] else main()
