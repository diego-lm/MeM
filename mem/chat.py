"""Motor de conversación: contexto mínimo + loop de tools sobre la memoria.

Costo fijo por turno: NUCLEO + prompt del modo + resumen rodante + últimos N turnos.
Costo variable: solo lo que el modelo pide vía tools (tope max_paginas).
Los tokens de entrada se miden y quedan registrados en la sesión.
"""
import json
import re
import time
import unicodedata
from pathlib import Path

from . import agentes, crear
from . import llm as llm_mod
from . import mcps, memoria, modos, servicios, sesiones, skills, triaje, web

MAX_PASOS = 8
ULTIMOS_TURNOS = 6      # mensajes previos que se recargan de la sesión
RESUMEN_CADA = 4        # turnos entre actualizaciones del resumen rodante
RESUMEN_MAX = 1500

TOOLS = [
    {"name": "buscar",
     "description": "Busca términos en los índices de la base (categorías, índice temático, índice general). Devuelve líneas candidatas con su archivo. Usar SIEMPRE antes de leer páginas.",
     "parameters": {"type": "object", "properties": {"consulta": {"type": "string"}}, "required": ["consulta"]}},
    {"name": "leer_pagina",
     "description": "Lee una página de la base por su path relativo (ej. 06_Biblioteca_Conocimiento/Entradas/x.md). Las páginas largas vienen en partes: el final avisa y se sigue con parte=2, 3…",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                    "parte": {"type": "integer", "description": "para páginas largas; default 1"}},
                    "required": ["path"]}},
    {"name": "conexiones",
     "description": ("Conexiones de una entrada (slug de su ruta Entradas/<slug>.md): sesiones que la "
                     "citaron, entradas del mismo subject, [[wikilinks]] en ambos sentidos y relacionadas "
                     "por similitud semántica. Úsala tras buscar/leer_pagina para navegar el vecindario "
                     "de una memoria y traer contexto que la búsqueda no encontró."),
     "parameters": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}},
    {"name": "grep",
     "description": "Busca un patrón (texto o regex) en todos los .md de la base. Fallback cuando los índices no dan resultado.",
     "parameters": {"type": "object", "properties": {"patron": {"type": "string"}}, "required": ["patron"]}},
]

# No se filtra por modo (va suelta en `responder`): el proyecto es de la sesión,
# no de la vista, y no queremos tocar los .md de Modos de la base de Diego.
TOOL_PROYECTO = {
    "name": "fijar_proyecto",
    "description": ("Asigna o cambia el proyecto de esta sesión cuando Diego dice en cuál está "
                    "trabajando o dónde quiere guardar lo que están hablando. Si el nombre no "
                    "está en la lista de proyectos, pregúntale antes si quiere crearlo — y si tiene "
                    "que ser privado (solo se lee parado en él). Omití `privado` si el proyecto ya "
                    "existe y Diego no dijo nada de cambiarle eso: sin el parámetro no se toca."),
    "parameters": {"type": "object", "properties": {
        "nombre": {"type": "string"},
        "privado": {"type": "boolean"}},
        "required": ["nombre"]},
}

# Siempre disponible (como fijar_proyecto): cuando Diego dice "usa la foto que
# subí" o "el video que generaste", el agente tiene que poder ABRIR ese medio,
# no solo saber su nombre. Mismo lector que el inbox (media.extraer).
TOOL_MEDIO = {
    "name": "leer_medio",
    "description": ("Lee un medio guardado en la base (imagen, video, audio o PDF) y devuelve su "
                    "contenido como texto: imagen/video descritos por visión, audio transcrito. "
                    "`path` = ruta relativa dentro de la base (ej. 07_Inbox/_adjuntos/foto.jpg); "
                    "las entradas de la Biblioteca la traen en su frontmatter `adjunto`. Úsala "
                    "cuando Diego pida trabajar sobre algo subido o generado antes. Después de leerlo, "
                    "inclúyelo en tu respuesta como markdown ![...](/attach/<path>) para que se vea "
                    "en el chat (imagen visible, video/audio reproducibles) — no lo describas solo "
                    "en texto, salvo que Diego haya pedido específicamente una descripción."),
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"]},
}

# El chat ya renderiza ![...](/attach/...) como medio real (imagen visible, video/
# audio reproducibles) — el modelo solo tiene que ACORDARSE de mandarlo así en vez
# de describirlo en prosa. crear_imagen/crear_video ya lo piden en su propia
# description; esto cubre TODO lo demás (leer_medio, y sobre todo claude_code, que
# nunca ve esos tools: solo su MCP propio hacia este mismo server — pedido
# 2026-08-06: "siempre debe mostrarse el contenido de la manera más útil, clara y
# limpia").
INSTRUCCION_MEDIA = (
    "Si en tu respuesta mencionas o encontrás una imagen, video o audio de la base "
    "(por su `adjunto` en el frontmatter de una entrada, o leyéndolo), mostralo SIEMPRE "
    "con markdown ![...](/attach/<ruta relativa>) — no lo describas solo en texto salvo "
    "que Diego pida explícitamente una descripción. Es la forma más clara y directa: "
    "el chat lo renderiza como medio real (imagen visible, video/audio reproducibles). "
    # pedido 2026-08-07: pidió "la imagen del cohete de gomita", había varias, el
    # agente eligió una y ofreció "avisame si querés ver otra" — otro ida y vuelta
    # para algo que se resuelve mostrándolas. Se ven como miniaturas (md.js), así
    # que mostrar cinco cuesta lo mismo que mostrar una.
    "Si hay VARIAS que encajan con lo que pidió, mostrálas TODAS en la misma respuesta "
    "y que elija: se ven chicas, en miniatura, y con un clic se agrandan. No elijas por "
    "él ni ofrezcas 'avisame si querés ver otra'.")


def _system(root, modo: dict, resumen: str, subjects: list | None = None, proyecto: str = "") -> str:
    partes = [memoria.nucleo(root), "---", modo["prompt"], INSTRUCCION_MEDIA]
    if subjects:
        partes.append(f"Subjects de esta sesión: {', '.join(subjects)}. "
                      "Prioriza buscar y leer páginas de esos subjects.")
    proyectos = memoria.proyectos_listar(root)
    if proyectos:
        partes.append("Proyectos de Diego: " + ", ".join(
            f"{p['nombre']} ({'privado' if p['privado'] else 'público'})" for p in proyectos) + ".")
    if proyecto:
        partes.append(
            f"Proyecto activo de esta sesión: {proyecto}. Lo que guardes acá queda dentro del "
            "proyecto. Si Diego trae algo nuevo que parece de otro proyecto —o que quizá no sea "
            "de este—, pregúntale a cuál va antes de guardarlo.")
    elif proyectos:
        partes.append("Esta sesión no tiene proyecto. Si lo que Diego cuenta parece pertenecer a "
                      "alguno de sus proyectos, pregúntale si lo asignas (fijar_proyecto).")
    if "skill" in (modo.get("herramientas") or []) and (idx := skills.indice(root)):
        partes += ["---", idx]
    if resumen:
        partes += ["---", f"Resumen de la sesión hasta ahora:\n{resumen}"]
    return "\n\n".join(partes)


def _herramientas(modo: dict) -> list[dict]:
    """Tools del modo: las de la base + las externas que pida su frontmatter.
    `web` y `skill` son locales; `mcp` levanta los servidores de mcp.json (si no
    hay archivo, no cuesta nada)."""
    pedidas = set(modo.get("herramientas") or [])
    tools = [t for t in TOOLS if t["name"] in pedidas]
    if "web" in pedidas:
        tools += web.TOOLS
    if "skill" in pedidas:
        tools += skills.TOOLS
    if "crear" in pedidas:
        tools += crear.TOOLS
    if "mcp" in pedidas:
        tools += mcps.tools()
    return tools


def _ejecutar(root, nombre: str, args: dict, paginas: list, max_paginas: int, ctx: dict) -> str:
    """ctx = {sid, proyecto} de la sesión; mutable porque fijar_proyecto cambia el
    proyecto a mitad de turno y lo que se guarde después debe caer en el nuevo."""
    # La sesión ES el proyecto desde el que se pregunta: lo privado de otro no
    # entra al contexto de este turno (pedido 2026-08-12). Va en las cuatro
    # lecturas, no solo en buscar: grep y leer_pagina llegan a los mismos .md.
    desde = str(ctx.get("proyecto") or "")
    if nombre == "buscar":
        return memoria.buscar(root, args.get("consulta", ""), proyecto=desde)
    if nombre == "leer_pagina":
        path = args.get("path", "")
        if len(paginas) >= max_paginas and path not in paginas:
            return f"(tope de {max_paginas} páginas por turno alcanzado; responde con lo ya leído)"
        try:
            parte = int(args.get("parte") or 1)
        except (TypeError, ValueError):
            parte = 1
        salida = memoria.leer_pagina(root, path, parte, proyecto=desde)
        if not salida.startswith("(") and path not in paginas:
            paginas.append(path)
        return salida
    if nombre == "conexiones":
        slug = args.get("slug", "")
        try:
            return json.dumps(memoria.conexiones(root, slug, proyecto=desde), ensure_ascii=False)
        except FileNotFoundError:
            return f"(entrada no encontrada: {slug})"
    if nombre == "grep":
        return memoria.grep(root, args.get("patron", ""), proyecto=desde)
    if nombre == "leer_medio":
        from . import media
        # el modelo suele copiar la URL del markdown previo (/attach/07_...): se acepta igual
        ruta = str(args.get("path") or "").strip().lstrip("/").removeprefix("attach/")
        cfg_chat = agentes.para(ctx["cfg"], "chat")
        contenido, faltante, _ = media.extraer(cfg_chat, root, ruta)
        if len(contenido) > media.MAX_CHARS:
            # la línea de tiempo de un video de una hora no entra en el contexto:
            # acá va resumida y la copia entera queda en la entrada, paginada
            contenido = (f"{media.condensar(cfg_chat, contenido)}\n\n"
                         "(resumen por tramos; la línea de tiempo completa está en la entrada "
                         "de este medio, se lee con leer_pagina y su parámetro `parte`)")
        return contenido or f"({faltante or 'medio vacío'})"
    if nombre == "fijar_proyecto":
        if not ctx.get("sid"):
            return "(no hay sesión activa a la que fijarle un proyecto)"
        try:
            privado = args.get("privado")
            p = memoria.proyecto_guardar(root, str(args.get("nombre") or ""),
                                         bool(privado) if privado is not None else None)
        except ValueError as e:
            return f"({e})"
        ctx["proyecto"] = p["nombre"]
        sesiones.actualizar_meta(root, ctx["sid"], proyecto=p["nombre"])
        return f"proyecto de la sesión: {p['nombre']} ({'privado' if p['privado'] else 'público'})"
    if nombre in crear.NOMBRES:
        # las de nube gastan créditos: la app frena acá y le pregunta a Diego con un
        # diálogo (pedido 2026-08-06). El diálogo devuelve los args con los que
        # generar —los del modelo más lo que Diego eligió ahí (resolución, duración:
        # pedido 2026-08-07)— o None si dijo que no. Sin canal de confirmación —MCP,
        # tests— se ejecuta como siempre: ahí el permiso lo da el cliente que hospeda
        # la tool.
        if nombre in crear.CONFIRMAR and (pedir := ctx.get("confirmar")):
            # Un "no" vale para TODO el turno: se contesta con el mismo texto y no
            # se vuelve a preguntar. Sin este cerrojo el modelo llamaba otra vez a
            # crear_nube y la app abría el diálogo de nuevo — el 2026-08-23 fueron
            # cinco, tres minutos cada uno esperando a nadie, y el turno se quedó
            # sin pasos antes de llegar a generar. Es el gemelo del cerrojo de
            # "hacelo en local" que ya vive en responder().
            if ya := ctx.get("rechazo_nube"):
                return ya
            # cualquier cosa que no sean args es un "no": el default de esta rama
            # tiene que ser NO gastar, aunque el que contesta devuelva otra cosa.
            # Si el "no" viene con texto, ese texto ES lo que eligió Diego en el
            # diálogo (p.ej. "hacelo en local") y va tal cual al modelo.
            if not isinstance(r := pedir(nombre, args), dict):
                ctx["rechazo_nube"] = r if isinstance(r, str) and r.strip() else crear.RECHAZO
                return ctx["rechazo_nube"]
            args = r
        return crear.ejecutar(ctx["cfg"], nombre, args, proyecto=str(ctx.get("proyecto") or ""),
                              sesion=str(ctx.get("sid") or ""), cancelar=ctx.get("cancelar"))
    if nombre in ("buscar_web", "leer_web"):
        return web.ejecutar(nombre, args)
    if nombre == "skill":
        return skills.leer(root, str(args.get("nombre") or ""))
    if nombre.startswith("mcp__"):
        return mcps.llamar(nombre, args)
    return f"(herramienta desconocida: {nombre})"


# El agente Cheap (modelo local, débil siguiendo instrucciones) a veces pide
# autorización para crear_imagen pese a que media.md ya dice explícitamente que
# no hace falta (es local y gratis) — visto en vivo 2026-08-06, dos veces, con
# el prompt ya corregido. No hay UI para "aprobar" esa pregunta suelta, así que
# en vez de dejarla sin salida se le contesta sola UNA vez. Acotado a
# crear_imagen: crear_video/crear_nube si deben preguntar (tardan o gastan
# créditos, por política de media.md), así que si las nombra no se auto-contesta.
RX_PIDE_AUTORIZACION = re.compile(r"autoriz|\bpermiso\b|\bapruebo\b|\bapruebes\b|\bapruebas\b", re.I)
# lo que identifica al pedido como generación LOCAL: no siempre nombra la tool
# ("¿Apruebo lanzar la generación con ComfyUI?" — visto 2026-08-06)
RX_LOCAL = re.compile(r"crear_imagen|comfyui|\bimagen\b", re.I)
# si nombra algo que TARDA o CUESTA, la pregunta puede ser legítima: no se toca
RX_PAGO = re.compile(r"crear_video|crear_nube|higgsfield", re.I)


def _pide_autorizacion_de_mas(texto: str) -> bool:
    if not ("?" in texto and RX_PIDE_AUTORIZACION.search(texto)):
        return False
    return bool(RX_LOCAL.search(texto)) and not RX_PAGO.search(texto)


# Peor todavía (visto en vivo el mismo día): el agente a veces ni pregunta ni
# llama a la tool — narra un "✅ Imagen modificada" con un ![...](/attach/...)
# a un archivo que jamás generó. Un chequeo contra el disco lo distingue de un
# resultado real: si la ruta que cita no existe, no generó nada.
RX_ATTACH = re.compile(r"/attach/([^)\s\"']+)")


def _resultado_fantasma(root: Path, texto: str) -> bool:
    rutas = RX_ATTACH.findall(texto)
    return bool(rutas) and any(not (root / r).is_file() for r in rutas)


# Y el fallo simétrico, el que más molestó (2026-08-06): la tool generó de verdad
# y el modelo NO puso el markdown, así que el medio quedaba solo en la memoria y
# la sesión mostraba prosa. Que el thumbnail aparezca no puede depender de que el
# modelo se acuerde: el motor sabe qué rutas salieron del turno y las pega él.
# el markdown entero de un medio, con la ficha opcional entre comillas que escribe
# crear.catalogar: ![titulo](/attach/x.mp4 "minimax_h3 · Higgsfield (nube)")
RX_MD_MEDIO = re.compile(r"!\[[^\]]*\]\(/attach/([^)\s\"']+)[^)]*\)")


def _con_medios(texto: str, medios: list[str]) -> str:
    """`medios` = el markdown COMPLETO de cada medio del turno: así lo que se pega
    lleva también su ficha (con qué modelo y en qué servicio se generó)."""
    vistos = set(RX_ATTACH.findall(texto))
    faltan = [m for m in dict.fromkeys(medios) if not set(RX_MD_MEDIO.findall(m)) & vistos]
    return texto + "".join(f"\n\n{m}" for m in faltan)


# Tercera variante del mismo problema (2026-08-06, "qué imágenes tengo del cohete"):
# al LISTAR lo que ya existe, el modelo copia el link que devuelve `buscar` y le
# pone un ! delante — ![titulo](Entradas/x.md). Eso no es una imagen: el chat lo
# pintaba como texto pelado, sin una sola miniatura. La entrada sí sabe cuál es su
# adjunto, así que se corrige acá en vez de esperar que el modelo acierte.
RX_IMG_ENTRADA = re.compile(r"!\[([^\]]*)\]\((?:06_Biblioteca_Conocimiento/)?Entradas/([^)]+?)\.md\)")


def _medios_citados(root: Path, texto: str) -> str:
    def sub(m):
        try:                       # el slug puede no existir (el modelo lo inventó)
            e = memoria.leer_entrada(root, m.group(2))
        except OSError:
            e = {}
        # sin adjunto no hay nada que mostrar: al menos queda un link navegable
        if not (adj := str(e.get("adjunto") or "")):
            return f"[{m.group(1)}](Entradas/{m.group(2)}.md)"
        # lo ya catalogado también dice con qué se hizo: está en su frontmatter
        ficha = crear.ficha_corta(str(e.get("backend") or ""), str(e.get("modelo") or ""))
        return f"![{m.group(1)}](/attach/{adj}" + (f' "{ficha}")' if ficha else ")")
    return RX_IMG_ENTRADA.sub(sub, texto)


def responder(cfg: dict, modo: dict, mensajes: list[dict], resumen: str = "", on_event=None,
              subjects: list | None = None, on_result=None, tarea: str = "chat",
              sid: str = "", proyecto: str = "", agente: str = "", on_confirm=None,
              cancelar=None):
    """Un turno completo (sin persistencia). mensajes termina en el user msg nuevo.
    `tarea` decide qué agente configurado responde (agentes.py); `agente` fuerza uno
    (el botón "reintentar con otro agente" de la burbuja de error).
    `on_confirm(nombre, args) -> bool` es el diálogo de la app para las tools que
    gastan plata (crear.CONFIRMAR); sin él esas tools corren sin preguntar.
    `cancelar` es un threading.Event: el botón de detener del chat. Se mira entre
    pasos y dentro del polling de ComfyUI — el hilo del turno no es abortable, así
    que la cancelación es cooperativa (ponytail: la llamada al LLM que ya está en
    vuelo no se corta; lo que tarda de verdad —generar— sí, al instante).
    Devuelve (texto, paginas_usadas, tokens_entrada)."""
    root = cfg["hamuq"]
    # el modo puede fijar su propio agente (frontmatter `agente:`): el modo crear
    # necesita uno con tool-calling aunque el chat general use claude_code
    ag = agentes.para(cfg, str(modo.get("agente") or tarea), agente or None)
    servicios.preparar(cfg, ag, on_event)      # modelo local sin cargar: avisa y libera VRAM
    cliente = llm_mod.crear(ag)
    # el CLI de Claude crea medios por su MCP propio, fuera de este loop: sin esto
    # llamaba a crear_imagen sin sesión ni proyecto y la entrada quedaba suelta
    # (las 3 del cohete, 2026-08-06). Los demás proveedores lo ignoran.
    cliente.sesion, cliente.proyecto = sid, proyecto
    system = _system(root, modo, resumen, subjects, proyecto)
    if ag.get("system_prompt"):
        system = f"{ag['system_prompt']}\n\n---\n\n{system}"
    paginas: list[str] = []
    ctx = {"sid": sid, "proyecto": proyecto, "cfg": cfg, "confirmar": on_confirm, "cancelar": cancelar}
    tokens_in = 0
    medios: list[str] = []      # rutas que generaron las tools de este turno
    msgs = list(mensajes)
    scripted = modo.get("retrieval") == "scripted" or not getattr(cliente, "tools_ok", True)
    tools = [] if scripted else _herramientas(modo)   # sin tool-calling no se levanta nada
    if tools:
        tools = tools + ([TOOL_PROYECTO] if sid else []) + [TOOL_MEDIO]
    # El modo declara si este turno genera medios (media.md) — se mira el MODO y no
    # `tools`, porque con un proveedor sin tool-calling `tools` va vacío y aun así
    # se puede crear.
    puede_crear = "crear" in (modo.get("herramientas") or [])
    if puede_crear:
        # Ajustes › Media: el backend preferido por tipo y los job_types por defecto
        system += "\n\n" + crear.preferencias(cfg)
        if mat := _ultima_imagen(root, msgs):
            system += (f"\n\nMaterial a la vista en esta conversación: {mat}. Si Diego pide "
                       "modificarlo, animarlo o partir de él, pasá esa ruta como `referencia` "
                       "— el archivo real se manda al generador. NUNCA lo describas en el "
                       "prompt para que se reconstruya: el prompt dice solo el cambio pedido.")
    # A quién hay que vigilarle los pedidos de permiso: además del modo media, el CLI
    # de Claude Code en CUALQUIER modo — su MCP propio expone crear_imagen aunque el
    # modo no la declare, y de ahí salía "necesito tu permiso para lanzar la
    # generación con ComfyUI (mcp__mem__crear_imagen)" en modo chat (visto 2026-08-06).
    vigilar_permisos = puede_crear or getattr(cliente, "mcp_propio", False)

    if scripted:
        # para modelos locales débiles en tool-calling: el motor busca y precarga
        contexto = _retrieval_scripted(root, msgs[-1]["content"], modo, paginas, proyecto)
        msgs = msgs[:-1] + [{"role": "user", "content": f"{contexto}\n\n---\n\n{msgs[-1]['content']}"}]

    # claude_code no llama a estas tools sino a las de su MCP: lo generado no pasa
    # por `medios` y hay que ir a buscarlo a la memoria (por eso 1.5 le pasa el sid).
    t0 = time.time() if (getattr(cliente, "mcp_propio", False) and sid) else 0.0

    def cerrar(texto: str) -> str:
        """Texto final del turno, con los medios (generados o citados) sí o sí visibles."""
        texto = _medios_citados(root, texto)
        extra = list(medios)
        if t0 and "/attach/" not in texto:
            # claude_code generó por su MCP: acá solo se conoce la ruta, sin ficha
            extra += [f"![resultado](/attach/{r})" for r in memoria.adjuntos_de_sesion_desde(root, sid, t0)]
        return _con_medios(texto, extra)

    correcciones = 0   # tope combinado para los dos parches de abajo: nunca más de 2 por turno
    forzar = ""   # nombre de tool a forzar (tool_choice) en el intento siguiente, si corresponde
    backend = ""  # lo que Diego ya contestó en el diálogo de este turno ("no, en local")
    for _ in range(MAX_PASOS):
        if cancelar is not None and cancelar.is_set():
            return "(turno cancelado)", paginas, tokens_in
        r = cliente.completar(system, msgs, tools or None, **({"forzar_tool": forzar} if forzar else {}))
        forzar = ""
        tokens_in += r["tokens_in"]
        if not r["tool_calls"]:
            aviso = None
            if correcciones < 2 and vigilar_permisos:
                if _pide_autorizacion_de_mas(r["texto"]):
                    aviso = "Sí, autorizado — no hace falta que preguntes, procedé directo."
                elif _resultado_fantasma(root, r["texto"]):
                    aviso = ("Esa ruta no existe: no generaste nada todavía. Llamá de verdad a la tool "
                             "crear_imagen en vez de describir un resultado que no pasó.")
            if aviso:
                correcciones += 1
                msgs.append({"role": "assistant", "content": r["texto"]})
                msgs.append({"role": "user", "content": aviso})
                # no alcanza con pedirlo por texto (el modelo puede inventar una
                # excusa nueva) — el intento siguiente fuerza la tool por API,
                # así que ese paso sí o sí llama a crear_imagen o falla con un
                # error real del proveedor, nunca con otra excusa en prosa. Solo
                # si el proveedor recibe tools: sin tool-calling no hay qué forzar.
                forzar = "crear_imagen" if any(t["name"] == "crear_imagen" for t in tools) else ""
                continue
            return cerrar(r["texto"]), paginas, tokens_in
        msgs.append({"role": "assistant", "content": r["texto"], "tool_calls": r["tool_calls"]})
        for tc in r["tool_calls"]:
            # Ajustes › Media manda: el modelo puede llamar a la local aunque Diego
            # haya elegido nube para ese tipo (y al revés, que además gasta). Se
            # corrige acá porque es el único lugar con las dos cosas a la vista: la
            # tool elegida y lo que Diego escribió en ESTE turno (pedido 2026-08-07).
            if tc["name"] in crear.NOMBRES:
                tc["name"], tc["args"] = crear.enrutar(cfg, tc["name"], tc["args"],
                                                       _texto_pedido(mensajes), backend)
            # el material señalado va como INPUT, no como prompt que lo describa
            # (pedido 2026-08-07). Se resuelve acá y no antes del paso porque una
            # creación de este mismo turno ya cuenta: msgs lleva su ![](/attach/…).
            if (tc["name"] in crear.CON_REFERENCIA and not tc["args"].get("referencia")
                    and (ref := _material_indicado(root, mensajes, msgs))):
                tc["args"]["referencia"] = ref
            if on_event:
                on_event(tc["name"], tc["args"])
            salida = _ejecutar(root, tc["name"], tc["args"], paginas, int(modo.get("max_paginas", 5)), ctx)
            if salida == crear.RECHAZO_LOCAL:
                # dijo "no a la nube, hacelo en local": lo que se genere en lo que
                # queda del turno va a ComfyUI. Sin esto el enrutado leía Ajustes
                # otra vez, mandaba de vuelta a la nube y el turno hacía ping-pong.
                backend = "comfyui"
            if on_result:
                on_result(tc["name"], tc["args"], salida)
            if tc["name"] in crear.NOMBRES:
                medios += [m.group(0) for m in RX_MD_MEDIO.finditer(salida) if (root / m.group(1)).is_file()]
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["name"], "content": salida})
            if cancelar is not None and cancelar.is_set():
                return cerrar("(turno cancelado)"), paginas, tokens_in
    return cerrar("(se alcanzó el límite de pasos del turno)"), paginas, tokens_in


def _retrieval_scripted(root, pregunta: str, modo: dict, paginas: list, proyecto: str = "") -> str:
    hits = memoria.buscar(root, pregunta, proyecto=proyecto)
    rutas = re.findall(r"\(([\w./ -]+?\.md)\)", hits)
    bloques = [f"[Índices]\n{hits}"]
    for ruta in dict.fromkeys(rutas):
        if len(paginas) >= int(modo.get("max_paginas", 5)):
            break
        texto = memoria.leer_pagina(root, ruta, proyecto=proyecto)
        if not texto.startswith("("):
            paginas.append(ruta)
            bloques.append(f"[Página: {ruta}]\n{texto[:6000]}")
    return "Contexto recuperado de la base:\n\n" + "\n\n".join(bloques)


def _con_adjuntos(cfg: dict, texto: str, adjuntos: list[str], on_evento=None, on_resultado=None) -> str:
    """Versión PARA EL MODELO del mensaje con sus adjuntos: cada uno se lee con el
    MISMO lector que el inbox (imagen y video por visión, audio transcrito, texto
    tal cual) y se pega al mensaje de este turno — en el historial persiste solo el
    markdown de los medios (turno() lo agrega); turnos futuros los releen con
    leer_medio. Lo que no sabe leer se dice en voz alta en vez de perderse en
    silencio. Son varios porque el compositor deja adjuntar varios (pedido
    2026-08-07): "mirá estas tres fotos" tiene que llegar con las tres.

    `on_evento`/`on_resultado`: mismo contrato que las tools (turno._ev/_res) — leer
    un adjunto puede tardar (visión/transcripción con un endpoint frío) y sin esto
    quedaba invisible: el chat mostraba "pensando…" sin ninguna señal de qué parte
    del turno estaba colgada (bug real 2026-08-09, ver llm.TIMEOUT_API)."""
    from . import media
    cfg_chat = agentes.para(cfg, "chat")
    for adjunto in adjuntos:
        if on_evento:
            on_evento("leer_adjunto", {"path": adjunto})
        contenido, faltante, _ = media.extraer(cfg_chat, cfg["hamuq"], adjunto)
        contenido = media.condensar(cfg_chat, contenido)   # un video largo no entra en el turno
        if on_resultado:
            on_resultado("leer_adjunto", {"path": adjunto}, f"(no leído: {faltante})" if faltante else "ok")
        nota = ""
        if Path(adjunto).suffix.lower() in media.IMAGENES + media.VIDEO:
            # sin esto el modelo mandaba a ComfyUI un prompt DESCRIBIENDO el adjunto
            # en vez del adjunto mismo: la referencia sube el archivo real
            nota = ("\n(Si Diego pide modificar, editar o animar este adjunto, pasa su ruta como "
                    "`referencia` a crear_imagen/crear_video: el archivo real se envía a ComfyUI. "
                    "El prompt describe solo el cambio pedido, no reconstruye el adjunto.)")
        if contenido:
            texto = f"{texto}\n\n[Adjunto {adjunto}]{nota}\n{contenido}"
        elif faltante:
            texto = f"{texto}\n\n[Adjunto {adjunto} — no se pudo leer: {faltante}]{nota}"
    return texto


# ------------------------------------------------- pedir medios desde cualquier modo
# Pedido de Diego (2026-08-05): pedir "generá la imagen de un caballo volador" en
# una sesión de chat no puede terminar en un "no puedo". El modo chat no tiene las
# herramientas de creación —y su agente (claude_code) ni siquiera hace tool-calling—,
# así que el turno lo atiende el modo media, con su prompt, sus tools y su agente.

_MEDIOS = (r"\b(imagen|imagenes|foto\w*|ilustracion|dibujo|video\w*|clip|gif|audio|voz|musica"
           r"|cancion|image|images|picture|photo|artwork|movie|song|voice|music)\b")

# Verbos de crear + el nombre de un medio cerca. A propósito sin "hacer"/"make":
# son los más ambiguos de todos ("hazme un resumen de la imagen") y con ellos el
# detector se dispara de más.
RX_CREAR = re.compile(
    r"\b(gener\w*|cre[ae]\w*|compon\w*|dise[nñ]\w*|creat\w*|generat\w*|design\w*)\b"
    r".{0,40}?" + _MEDIOS)

# Verbos que YA significan "hazme una imagen" sin nombrar el medio ("dibuja un
# logo"). Terminaciones explícitas y no `\w*`: `dibuj\w*` se comería el sustantivo
# "dibujo" y `anim\w*` se comería "animal".
RX_VISUAL = re.compile(
    r"\b(dibuj(a|ar|ame|es|en|alo|ala)|ilustr(a|ar|ame|es|alo|ala)"
    r"|renderiz(a|ar|ame|alo)|anima(r|me|lo|la)?|draw|illustrate|render)\b")

# Editar un medio que ya existe también es un pedido para el taller: ComfyUI tiene
# el workflow `editar` y crear_imagen acepta `referencia`. Sin esto, "busca la
# imagen del cohete y modifícala" se quedaba en modo chat con un agente sin tools
# (reportado 2026-08-06: se generaron 3 imágenes por fuera y no se vio ninguna).
_VERBO_EDITAR = (r"\b(modific\w*|edit(a|ar|ame|es|alo|ala)|cambi\w*|quit\w*|saca(r|le|me|lo|la)?"
                 r"|agreg\w*|retoc\w*|recort\w*|modify|edit|change|remove|crop)\b")
# En los dos órdenes: al editar, el medio suele nombrarse ANTES del verbo ("la
# imagen del cohete ... y la modifiques"), al revés que al crear.
RX_EDITAR = re.compile(f"{_VERBO_EDITAR}.{{0,40}}?{_MEDIOS}|{_MEDIOS}.{{0,60}}?{_VERBO_EDITAR}")

# Pedir algo SOBRE un medio (resumirlo, describirlo) es leer la memoria, no crear.
RX_LEER = re.compile(r"\b(resum\w*|describ\w*|analiz\w*|transcrib\w*|explic\w*|summar\w*|describe\w*)\b")

# Con un medio recién hecho sobre la mesa, los pedidos se vuelven telegráficos:
# "el mismo pero en 16:9 y que dure más", "reintentalo en higgsfield", "hazlo en
# comfyui local". Ninguno nombra un verbo de crear —y a veces ni el medio—, así
# que pide_medios los dejaba pasar de largo y los atendía el agente de chat, que
# no tiene herramientas: los TRES ajustes al video del 2026-08-07 murieron ahí,
# en un "necesito tu permiso" sin dónde contestar. Solo se miran cuando la
# conversación YA tiene un medio: sueltos no significan nada.
RX_AJUSTE = re.compile(
    r"\b(reintent\w*|rehac\w*|regener\w*|hazl[oa]|hacel[oa]|hagal[oa]"
    r"|higgs\w*|comfy\w*|en la nube|en local"
    r"|vertical|horizontal|apaisad\w*|\d{1,2}\s*:\s*\d{1,2}"
    r"|mas (largo|corto|lento|rapido|grande|chico|nitid[oa])|dure (mas|menos)"
    r"|mism[oa] \w+ pero|igual pero)\b")
# Nombrar algo que ya está guardado. Veta crear (nadie "genera" lo que ya tiene)
# pero NO editar: "modifica la foto que subí" es justamente cómo se nombra el origen.
RX_YA_EXISTE = re.compile(r"\bque (guarde|subi|te mande|tengo|hay)\b|\bde mi memoria\b")


# ------------------------------------------- usar el material, no describirlo
# Pedido 2026-08-07: Diego editó una imagen (bien, con `referencia`) y después
# pidió animarla; el modelo llamó a crear_video SIN referencia y el generador
# reconstruyó "un cohete parecido" desde el prompt. Cuando el material está
# señalado, el archivo real se manda como input aunque el modelo se olvide.
# Demostrativo PEGADO al nombre del medio a propósito: "un gato que esté
# volando" no señala nada, y un falso positivo genera desde la imagen equivocada.
RX_SENALA = re.compile(
    r"\b(es[ae]|est[ae]|aquell[ao]|mism[ao]|ultim[ao])\s+" + _MEDIOS
    + "|" + _MEDIOS + r"\s+(anterior|de (antes|arriba)"
    r"|que (generaste|hiciste|creaste|subi|mande|mandaste|acabas))")


def _ultima_imagen(root: Path, msgs: list[dict]) -> str:
    """Última imagen a la vista en la conversación: un adjunto de Diego, una
    referencia elegida en Media o una creación previa. Solo imagen — es lo que
    aceptan como entrada el workflow `editar`, `video-imagen` y Higgsfield."""
    from . import media
    for m in reversed(msgs):
        for ruta in reversed(RX_ATTACH.findall(str(m.get("content") or ""))):
            if Path(ruta).suffix.lower() in media.IMAGENES and (root / ruta).is_file():
                return ruta
    return ""


def _texto_pedido(mensajes: list[dict]) -> str:
    """Lo que escribió Diego en este turno, sin lo que el server le pegó encima: el
    markdown del adjunto y su lectura por visión ("esta imagen muestra…")."""
    contenido = str(mensajes[-1].get("content") or "") if mensajes else ""
    return re.split(r"\n\n(?:!\[|\[Adjunto )", contenido)[0]


def _material_indicado(root: Path, mensajes: list[dict], msgs: list[dict]) -> str:
    """Ruta que hay que pasar como `referencia`, o "" si Diego no señaló nada.

    Señalar es una de dos: el mensaje TRAE el medio (adjunto, o referencias
    elegidas en Media — el cliente las pega como markdown), o el texto apunta a
    algo que ya está a la vista ("animá esa imagen").
    ponytail: si el pedido trae material y aun así es una creación desde cero, el
    modelo tiene que mandar `referencia` vacía explícita; hoy no puede — mandar
    el archivo es lo que Diego pidió, y equivocarse por defecto para el otro lado
    fue justo el bug.
    """
    contenido = str(mensajes[-1].get("content") or "") if mensajes else ""
    pedido = _texto_pedido(mensajes)
    if not (RX_SENALA.search(_sin_tildes(pedido)) or "/attach/" in contenido[len(pedido):]):
        return ""
    return _ultima_imagen(root, msgs)


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def pide_medios(texto: str, con_medio: bool = False) -> bool:
    """¿Es un pedido de GENERAR o EDITAR una imagen, un video o un audio?

    Heurística a propósito (no una llamada más al modelo): tiene que aparecer un
    verbo de crear/editar y, cerca, el nombre de un medio. Se prefiere quedarse
    corto — un falso negativo se arregla repitiendo el pedido o cambiando el modo
    a mano; un falso positivo secuestra una pregunta a la memoria y la manda al
    taller.
    `con_medio` = la última respuesta mostró un medio: ahí también valen los
    ajustes telegráficos (RX_AJUSTE), que solos no dirían nada.
    """
    t = _sin_tildes(texto)
    if RX_LEER.search(t):
        return False
    if RX_EDITAR.search(t) or (con_medio and RX_AJUSTE.search(t)):
        return True
    return bool(RX_CREAR.search(t) or RX_VISUAL.search(t)) and not RX_YA_EXISTE.search(t)


def _hay_medio(historial: list[dict]) -> bool:
    """¿Quedó un medio sobre la mesa? Lo dice la ÚLTIMA respuesta del asistente:
    lo que se ajusta es lo último que se generó, no algo de veinte turnos atrás."""
    for m in reversed(historial):
        if m["rol"] == "Asistente":
            return "/attach/" in str(m.get("texto") or "")
    return False


def _modo_para(root, modo: dict, pedido: str, con_medio: bool = False) -> tuple[dict, bool]:
    """(modo con el que responder, si hubo cambio a media)."""
    if "crear" in (modo.get("herramientas") or []) or not pide_medios(pedido, con_medio):
        return modo, False
    try:
        media = modos.cargar(root, "media")
    except OSError:
        return modo, False          # sin modo media instalado, se responde como se pueda
    # se comprueba que el modo media REALMENTE pueda crear: si alguien le sacó las
    # herramientas desde su frontmatter, cambiar de modo no arreglaría nada
    return (media, True) if "crear" in (media.get("herramientas") or []) else (modo, False)


# ------------------------------------------------- qué pasó durante el turno
# Pedido 2026-08-07: "esos mensajes de sistema deben ocultarse, pero indicarse en
# una pestaña del chat bubble, para poder expandirlo y ver lo que ocurrió".
# Se guarda al final del mensaje del Asistente, detrás de un marcador propio: el
# .md se sigue leyendo solo (son bullets) y el chat lo separa del texto para
# plegarlo. Un solo productor —turno()— y ningún cambio en responder().
MARCA_PROCESO = "···proceso"
# Un turno que falló también deja mensaje (antes el error vivía solo en la
# pantalla y se perdía al escribir otra cosa). Con esta marca al principio, el
# reintento sabe que ese turno no contestó nada.
MARCA_FALLO = "⚠ "


def _dur(segundos: float) -> str:
    if segundos < 2:
        return ""
    return f"{segundos:.0f} s" if segundos < 90 else f"{segundos / 60:.0f} min"


def _paso(nombre: str, args: dict, salida: str, segundos: float) -> str:
    """Una línea del registro: qué se llamó, con qué, cuánto tardó y cómo salió.
    Corta a propósito — es un registro de lo que pasó, no la salida entera."""
    detalle = str(args.get("modelo") or args.get("workflow") or args.get("prompt")
                  or args.get("consulta") or args.get("path") or args.get("patron") or "")
    salida = " ".join(str(salida or "").split())
    # las tools devuelven los fallos entre paréntesis (crear.py, memoria.py)
    resultado = f"⚠ {salida[1:120].rstrip(')')}" if salida.startswith("(") else "ok"
    return " · ".join(x for x in (nombre, detalle[:70], _dur(segundos), resultado) if x)


def _con_proceso(texto: str, pasos: list[str]) -> str:
    return f"{texto}\n\n{MARCA_PROCESO}\n" + "\n".join(f"- {p}" for p in pasos) if pasos else texto


def _linea_confirmacion(args: dict, respuesta) -> str:
    """La pregunta de la nube y lo que Diego contestó, como texto del turno y no
    como un diálogo que se desvanece (pedido 2026-08-07: 'debe quedar el texto de
    la pregunta y la respuesta como textos evidentes')."""
    # el modelo puede venir del diálogo (cuando Ajustes no fija un job_type por defecto)
    elegido = (respuesta.get("modelo") if isinstance(respuesta, dict) else "") or args.get("modelo")
    q = f"¿Generar `{elegido or 'esto'}` en la nube (gasta créditos)?"
    if isinstance(respuesta, dict):
        ps = " · ".join(f"{k} {v}" for k, v in (respuesta.get("params") or {}).items())
        return f"> ⚙ {q} — **Sí, generar**" + (f" · {ps}" if ps else "")
    if respuesta == crear.RECHAZO_LOCAL:
        return f"> ⚙ {q} — **No: generarlo en local**"
    if respuesta == crear.SIN_RESPUESTA:
        # que nadie conteste NO es lo mismo que cancelar, y la sesión lo decía
        # igual: leyendo el turno después no había forma de saber por qué murió
        return f"> ⚙ {q} — **Nadie contestó: cancelado**"
    return f"> ⚙ {q} — **No, cancelar**"


def turno(cfg: dict, sid: str, texto_usuario: str, on_event=None, adjuntos=(),
          agente: str = "", on_confirm=None, on_result=None, cancelar=None):
    """Turno con persistencia: carga sesión (resumen + últimos N), responde, guarda."""
    root = cfg["hamuq"]
    pedido = texto_usuario          # el texto de Diego, antes de pegarle los adjuntos
    # Los adjuntos quedan en el historial como markdown (la app los muestra como
    # medio embebido, igual que los generados); la descripción extraída viaja
    # SOLO al modelo en este turno (abajo) — no ensucia la burbuja de Diego.
    adjuntos = [a for a in adjuntos if a]
    for a in adjuntos:
        texto_usuario = f"{texto_usuario}\n\n![{Path(a).name}](/attach/{a})".strip()
    meta, historial = sesiones.cargar(root, sid)

    def _guardar(rol: str, texto: str) -> None:
        nonlocal historial
        sesiones.agregar_mensaje(root, sid, rol, texto)
        historial = [*historial, {"rol": rol, "texto": texto}]
        memoria.sincronizar_sesion_inbox(root, sid, _dialogo(historial),
                                         subjects=list(meta.get("subjects") or []),
                                         proyecto=str(meta.get("proyecto") or ""))

    modo = modos.cargar(root, str(meta.get("modo") or "chat"))
    modo, cambio = _modo_para(root, modo, pedido, _hay_medio(historial))
    if cambio and on_event:
        on_event("modo_media", {"desde": str(meta.get("modo") or "chat")})
    # Lo que escribió Diego se guarda ANTES de pedirle nada al modelo: si el
    # proveedor falla (sin crédito, sin conexión, modelo caído), el mensaje sigue
    # en la sesión al volver a abrirla. Antes se perdía al cambiar de pantalla
    # —se escribía recién después de responder— y con él la pregunta entera
    # (reportado 2026-08-05).
    # El reintento manda el mismo texto: si ya está guardado y todavía sin
    # respuesta, no se duplica. Un fallo del turno anterior queda como mensaje del
    # Asistente (abajo) y no cuenta como respuesta: si no, reintentar duplicaba la
    # pregunta de Diego en el archivo.
    previos = [m for m in historial if not (m["rol"] == "Asistente" and m["texto"].startswith(MARCA_FALLO))]
    if not (previos and previos[-1]["rol"] == "Diego" and previos[-1]["texto"] == texto_usuario):
        _guardar("Diego", texto_usuario)
    if meta.get("estado") == "temporal":
        # Una sesión con la que YA se habló no es un borrador: sale de "temporal"
        # ACÁ, junto con el mensaje, y no al terminar el turno. Mientras el modelo
        # pensaba —22 min con una generación de por medio— sesiones.listar() la
        # seguía filtrando, así que la conversación existía en el inbox pero NO en
        # /sessions: desde el escritorio no había forma de encontrarla ni de
        # contestarle el diálogo de la nube (reportado 2026-08-23).
        meta["estado"] = "activa"
        sesiones.actualizar_meta(root, sid, estado="activa")
    # lo ya destilado a memoria no vuelve a entrar al contexto: pasar a memoria
    # ACHICA el turno de verdad (el resumen rodante conserva el hilo)
    desde = int(meta.get("destilado_hasta") or 0)
    msgs = [{"role": "user" if m["rol"] == "Diego" else "assistant", "content": m["texto"]}
            for m in historial[max(desde, len(historial) - ULTIMOS_TURNOS):]]
    resumen = str(meta.get("resumen") or "")

    # El registro de lo que pasa durante el turno se arma acá, envolviendo los
    # callbacks que ya existían: responder() y _ejecutar() no se enteran. Va ANTES
    # de leer los adjuntos (abajo) para que esa lectura —que puede tardar, visión
    # con un endpoint frío— también quede como paso visible y no como un cuelgue mudo.
    pasos: list[str] = []
    visibles: list[str] = []     # lo que NO se pliega: preguntas hechas y contestadas
    relojes: dict[str, float] = {}
    t_turno = time.monotonic()

    def _ev(nombre, args):
        relojes[nombre] = time.monotonic()
        if on_event:
            on_event(nombre, args)

    def _res(nombre, args, salida):
        pasos.append(_paso(nombre, args, salida, time.monotonic() - relojes.pop(nombre, time.monotonic())))
        if on_result:
            on_result(nombre, args, salida)

    def _conf(nombre, args):
        r = on_confirm(nombre, args)
        visibles.append(_linea_confirmacion(args, r))
        return r

    if adjuntos:
        msgs[-1] = {**msgs[-1], "content": _con_adjuntos(cfg, msgs[-1]["content"], adjuntos, _ev, _res)}

    try:
        respuesta, paginas, tokens_in = responder(cfg, modo, msgs, resumen, _ev,
                                                  list(meta.get("subjects") or []), sid=sid,
                                                  proyecto=str(meta.get("proyecto") or ""),
                                                  agente=agente, on_confirm=_conf if on_confirm else None,
                                                  on_result=_res, cancelar=cancelar)
    except Exception as e:
        # El fallo no se pierde al escribir otra cosa ni al reintentar: queda en la
        # sesión como una respuesta más, corto arriba y con el detalle plegado
        # debajo (pedido 2026-08-07 — antes vivía solo en la pantalla).
        from . import fallas
        d = fallas.explicar(e)
        _guardar("Asistente", _con_proceso(
            f"{MARCA_FALLO}{d['problema']}\n\n{d['sugerencia']}", [*pasos, d["detalle"]]))
        raise

    if cancelar is not None and cancelar.is_set():
        # cancelado: no se guarda respuesta ni se cuenta el turno. El mensaje de
        # Diego ya está en la sesión sin contestar, que es justo lo que el chat
        # lee como "pendiente" y ofrece reintentar.
        return respuesta, paginas, tokens_in

    # Línea de cierre del registro: cuánto tardó el turno entero y con qué agente,
    # para poder distinguir "el modelo piensa despacio" de "el proveedor está
    # colgado" sin salir de la app (pedido 2026-08-09). agentes.para() releído acá
    # (no viaja en el retorno de responder(), que ya tiene 3 llamadores y un
    # contrato de tupla fijo) — es una lectura de config, no red.
    ag = agentes.para(cfg, str(modo.get("agente") or "chat"), agente or None)
    dur = time.monotonic() - t_turno
    pasos.append(f"⏱ turno · {ag.get('proveedor', '')}/{ag.get('modelo', '')} · {dur:.0f}s · {tokens_in} tokens")
    memoria.log_evento(root, "chat",
                       f"{sid}: turno {dur:.0f}s · {ag.get('proveedor', '')}/{ag.get('modelo', '')} · {len(pasos)} pasos")

    # lo que se pregunta y se contesta queda escrito (arriba, visible); lo técnico
    # queda al final, detrás del marcador que el chat pliega. `respuesta` sigue
    # limpia: es lo que se resume más abajo.
    guardado = _con_proceso("\n\n".join([*visibles, respuesta]), pasos)
    _guardar("Asistente", guardado)
    turnos = int(meta.get("turnos", 0)) + 1
    campos = {
        "turnos": turnos,
        "paginas_usadas": list(dict.fromkeys(list(meta.get("paginas_usadas") or []) + paginas)),
        "tokens_entrada_total": int(meta.get("tokens_entrada_total", 0)) + tokens_in,
        "tokens_entrada_ultimo_turno": tokens_in,
        # cuánto de ese turno es conversación, o sea lo ÚNICO que "pasar a memoria"
        # puede sacar del contexto (chars/4 ≈ tokens, alcanza para decidir). En un
        # modo con muchas herramientas el system prompt solo ya llena la ventana y
        # destilar no bajaría nada: el aviso lo mira para no pedirlo al pedo.
        "tokens_historial_ultimo_turno": sum(len(m["content"]) for m in msgs) // 4,
        # ventana real del modelo en uso (settings incluidos): el front compara
        # contra tokens_entrada_ultimo_turno y avisa cuando se está llenando
        "contexto_max": llm_mod.contexto_max(agentes.para(cfg, str(modo.get("agente") or "chat"))),
    }
    if cambio:
        # la sesión SE QUEDA en media: pedir una imagen casi nunca es un pedido
        # suelto, y volver a chat es un clic en el selector de la cabecera
        campos["modo"] = str(modo.get("nombre") or "media")
    if turnos == 1 and meta.get("titulo") in (None, "", "sesion"):
        # el texto crudo, no el markdown de los adjuntos; solo adjuntos = el nombre del primero
        campos["titulo"] = (pedido or (Path(adjuntos[0]).name if adjuntos else ""))[:48]
    # los subjects se descubren solos: los declara lo que se leyó, y si no se leyó
    # nada de la base (web, conocimiento del modelo) se miran los del árbol nombrados.
    nuevos = memoria.subjects_de(root, paginas) or memoria.subjects_mencionados(root, texto_usuario)
    previos = [str(s) for s in (meta.get("subjects") or [])]
    if (union := list(dict.fromkeys(previos + nuevos))) != previos:
        campos["subjects"] = union
    if turnos % RESUMEN_CADA == 0:
        dialogo = msgs + [{"role": "assistant", "content": respuesta}]
        campos["resumen"] = _resumir(cfg, resumen, dialogo)
        # La sesión también se cataloga sola: los subjects salían de lo leído,
        # pero los tags no los escribía NADIE desde el chat (10 de 16 sesiones ni
        # tenían el campo). Se re-evalúan sobre el resumen recién hecho, así que
        # una conversación que se corre de tema gana los tags nuevos sin perder
        # los viejos. Cuesta una llamada del agente rápido cada 4 turnos.
        ev = triaje.evaluar(cfg, campos["resumen"])
        if nuevos_tags := [t for t in ev.get("tags") or [] if t not in (meta.get("tags") or [])]:
            campos["tags"] = [*(str(t) for t in meta.get("tags") or []), *nuevos_tags]
    sesiones.actualizar_meta(root, sid, **campos)
    return guardado, paginas, tokens_in


def _resumir(cfg: dict, resumen_previo: str, msgs: list[dict]) -> str:
    cliente = llm_mod.crear(agentes.para(cfg, "resumir"))
    dialogo = "\n".join(f"{m['role']}: {m['content'][:500]}"
                        for m in msgs if m["role"] in ("user", "assistant") and not m.get("tool_calls"))
    prompt = (f"Resumen previo de la sesión:\n{resumen_previo or '(ninguno)'}\n\n"
              f"Últimos mensajes:\n{dialogo}\n\n"
              f"Escribe el resumen actualizado de la sesión en ≤{RESUMEN_MAX} caracteres, en español, "
              "denso en datos (temas tratados, decisiones, pendientes). Responde solo con el resumen.")
    r = cliente.completar("Eres un resumidor de sesiones de trabajo.", [{"role": "user", "content": prompt}], None)
    return r["texto"].strip()[:RESUMEN_MAX]


def _dialogo(historial: list[dict]) -> str:
    return "\n\n".join(f"[{m['rol']}]\n{m['texto']}" for m in historial)


def destilar(cfg: dict, sid: str) -> dict:
    """'Pasar a memoria': sincroniza la sesión entera al inbox (ya pasa en cada
    turno; esto fuerza un sync inmediato) y avanza el cursor de contexto — lo
    anterior sale del prompt del próximo turno. La promoción a la Biblioteca la
    hace el pipeline normal de procesar.py, no esto."""
    root = cfg["hamuq"]
    meta, historial = sesiones.cargar(root, sid)
    if not historial:
        return {"sincronizado": False, "estado": ""}
    iid = memoria.sincronizar_sesion_inbox(root, sid, _dialogo(historial),
                                           subjects=list(meta.get("subjects") or []),
                                           proyecto=str(meta.get("proyecto") or ""))
    # lo que midió el último turno ya no describe lo que se va a mandar (el
    # próximo arranca desde el corte): sin este reset el aviso seguía marcando
    # el contexto casi lleno con la sesión ya sincronizada
    sesiones.actualizar_meta(root, sid, destilado_hasta=len(historial),
                             tokens_entrada_ultimo_turno=0, tokens_historial_ultimo_turno=0)
    return {"sincronizado": True, "estado": "pendiente", "inbox": iid}


def demo():
    """Lo único no obvio de este módulo sin red: a qué pedidos se enciende el taller."""
    si = ["genera una imagen de un cohete",
          "dibuja un logo para el proyecto",
          "quítale el fondo a la foto del cohete",
          "busca la imágen del cohete gummy y modifícala para que no se vean los ositos",
          "modifica la foto que subí ayer",
          "recorta el video del final",
          "cambiale el color a la imagen"]
    no = ["describe la imagen que subí",
          "resume el video de la reunión",
          "qué fotos tengo de Bruselas",
          "cambia el modo de la sesión",
          "generá un resumen de lo que hablamos",
          "hazme un resumen de la imagen"]
    for t in si:
        assert pide_medios(t), f"debería ir al taller: {t}"
    for t in no:
        assert not pide_medios(t), f"NO debería ir al taller: {t}"

    # Los ajustes al medio recién hecho (2026-08-07): con el video a la vista son
    # pedidos para el taller; sueltos no significan nada y no pueden secuestrar
    # una conversación cualquiera.
    ajustes = ["puedes hacer el mismo video pero en formato 16:9 y que dure más tiempo?",
               "re intentalo en higgsfield",
               "si, hazlo en comfyUI local",
               "hacelo vertical",
               "igual pero más corto"]
    for t in ajustes:
        assert pide_medios(t, con_medio=True), f"con un medio a la vista va al taller: {t}"
        assert not pide_medios(t, con_medio=False), f"sin medio a la vista NO: {t}"
    # y el veto de leer sigue mandando aunque haya medio
    assert not pide_medios("explicame otra vez cómo lo hiciste", con_medio=True)
    assert _hay_medio([{"rol": "Diego", "texto": "x"},
                       {"rol": "Asistente", "texto": "listo ![a](/attach/a.png)"}])
    # lo que manda es la ÚLTIMA respuesta: el medio de hace tres turnos ya no está
    assert not _hay_medio([{"rol": "Asistente", "texto": "![a](/attach/a.png)"},
                           {"rol": "Diego", "texto": "gracias"},
                           {"rol": "Asistente", "texto": "de nada"}])
    assert not _hay_medio([{"rol": "Diego", "texto": "hola"}])

    # el registro del turno: se pliega detrás del marcador y los fallos se ven
    assert _con_proceso("hola", []) == "hola"
    p = _paso("crear_video", {"workflow": "video-imagen"}, "![x](/attach/x.mp4)\nentrada creada", 720)
    assert p == "crear_video · video-imagen · 12 min · ok", p
    assert "⚠ no se pudo generar" in _paso("crear_nube", {"modelo": "veo3_1"}, "(no se pudo generar: x)", 1)
    # la pregunta y la respuesta quedan escritas, no se desvanecen con el diálogo
    assert "**Sí, generar** · duration 8" in _linea_confirmacion({"modelo": "veo3_1"}, {"params": {"duration": 8}})
    # el modelo elegido EN el diálogo (Ajustes sin job_type) también queda escrito
    assert "`veo3_1`" in _linea_confirmacion({"modelo": ""}, {"modelo": "veo3_1", "params": {}})
    assert "**No: generarlo en local**" in _linea_confirmacion({"modelo": "veo3_1"}, crear.RECHAZO_LOCAL)
    assert "**No, cancelar**" in _linea_confirmacion({"modelo": "veo3_1"}, None)
    # nadie contestó ≠ cancelar: el turno tiene que poder decir cuál de las dos fue
    assert "Nadie contestó" in _linea_confirmacion({"modelo": "veo3_1"}, crear.SIN_RESPUESTA)

    # un "no" a la nube cierra el diálogo para todo el turno: el segundo intento
    # del modelo no vuelve a preguntar (2026-08-23: cinco diálogos de 3 min)
    veces = []
    ctx_nube = {"cfg": {}, "sid": "", "confirmar": lambda n, a: (veces.append(n), None)[1]}
    args_nube = {"modelo": "soul", "prompt": "x"}
    assert _ejecutar(Path("."), "crear_nube", args_nube, [], 5, ctx_nube) == crear.RECHAZO
    assert _ejecutar(Path("."), "crear_nube", args_nube, [], 5, ctx_nube) == crear.RECHAZO
    assert veces == ["crear_nube"], veces

    # el thumbnail se pega solo cuando el modelo se lo olvidó, y una sola vez —
    # con su ficha ("con qué modelo, en qué servicio"), que viaja en el markdown
    ficha = '![Video](/attach/a/b.mp4 "minimax_h3 · Higgsfield (nube)")'
    assert _con_medios("listo", [ficha]) == f"listo\n\n{ficha}"
    assert _con_medios("mirá ![x](/attach/a/b.mp4)", [ficha]) == "mirá ![x](/attach/a/b.mp4)"
    assert _con_medios("listo", [ficha, ficha]).count("!") == 1
    assert RX_MD_MEDIO.findall(ficha) == ["a/b.mp4"], "la ruta no puede llevarse la ficha pegada"

    # señalar material existente ≠ pedir algo nuevo: lo primero manda el ARCHIVO
    # como referencia, lo segundo no puede arrastrar la imagen anterior
    for t in ["anima esa imagen", "convertí ese video en gif", "usa la misma foto",
              "la imagen anterior pero en azul", "el video que generaste, mas corto"]:
        assert RX_SENALA.search(_sin_tildes(t)), f"señala material: {t}"
    for t in ["genera una imagen de un gato que este volando", "dibuja un logo",
              "crea un video de un cohete despegando", "esta bien, guardalo"]:
        assert not RX_SENALA.search(_sin_tildes(t)), f"NO señala nada: {t}"
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ruta = "07_Inbox/_adjuntos/f.png"
        (Path(tmp) / ruta).parent.mkdir(parents=True)
        (Path(tmp) / ruta).write_bytes(b"x")
        vista = [{"content": f"![x](/attach/{ruta})"}]
        # (a) el mensaje TRAE el medio (adjunto o elegido en Media): va como input
        assert _material_indicado(Path(tmp), [{"content": f"animá esto\n\n![f](/attach/{ruta})"}], vista) == ruta
        # (b) el texto señala algo que ya está a la vista
        assert _material_indicado(Path(tmp), [{"content": "anima esa imagen"}], vista) == ruta
        # (c) pedido nuevo: no puede arrastrar la imagen anterior
        assert _material_indicado(Path(tmp), [{"content": "genera una imagen de un gato"}], vista) == ""
        # (d) el detector mira SOLO lo que escribió Diego: la lectura por visión
        # que el server pega debajo dice "Esta imagen muestra…" y abriría la puerta sola
        sucio = "genera una imagen de un gato\n\n[Adjunto x.png]\nEsta imagen muestra un cohete."
        assert _material_indicado(Path(tmp), [{"content": sucio}], vista) == ""

    # el link de `buscar` con un ! delante no es una imagen: o se resuelve al adjunto
    # de esa entrada, o se degrada a link — nunca queda "![x](Entradas/…)" en pantalla
    assert _medios_citados(Path("/no/existe"), "![Nota](Entradas/fantasma.md)") == "[Nota](Entradas/fantasma.md)"
    assert _medios_citados(Path("/no/existe"), "sin cambios") == "sin cambios"
    print(f"ok — {len(si) + len(no)} pedidos clasificados, thumbnails garantizados")


if __name__ == "__main__":
    demo()
