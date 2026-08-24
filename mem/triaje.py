"""Triaje de captura: un agente revisa lo que el usuario acaba de escribir antes
de encolarlo en el inbox.

Dos caminos (spec de Diego):
  - La captura se explica sola (URL, empresa, contexto suficiente) -> se guarda
    directo y se avisa que quedó grabada.
  - La captura es un apunte pelado (un nombre suelto, un pegado sin contexto) ->
    se pregunta si va tal cual con fecha/lugar, o con tags/subject.

Siempre devuelve {mensaje, guardar, payload}. El frontend conversa hasta que
`guardar` es True y ahí encola en el inbox: nada se pierde ni se guarda a medias.
"""
import json
import re

from . import agentes
from . import llm as llm_mod
from . import memoria

MAX_HISTORIAL = 12          # mensajes de la conversación que se re-envían
MAX_SUBJECTS = 60           # subjects que caben en el prompt sin inflarlo

PROMPT = """Eres el triaje de captura de MeM, la memoria personal de Diego.

Diego acaba de escribir algo para guardar. Tu trabajo es decidir si se guarda ya
o si conviene preguntarle una sola cosa antes.

GUARDA DIRECTO (guardar: true) cuando la captura se explica sola:
contiene una URL, un nombre de empresa/producto/persona identificable, una idea
o nota con contexto suficiente, o cualquier cosa que dentro de un año se
entienda sin más. Extrae tú los tags y subjects que correspondan y confirma en
una frase corta que quedó grabada, invitando a agregar más datos si quiere.

PREGUNTA (guardar: false) solo cuando la captura es un apunte pelado sin
contexto: un nombre suelto, una palabra, un pegado corto que no dice de qué es.
Pregunta si lo guarda tal cual (queda con fecha y hora automáticas) o si prefiere
agregarle tags o algún subject de los existentes. Ofrece 2 o 3 subjects
plausibles de la lista. Una pregunta breve, nada de interrogatorios.

Si Diego ya respondió y pidió guardar, guarda (guardar: true) con lo acordado,
aunque siga siendo un apunte pelado: su decisión manda.

Subjects existentes:
{subjects}

{proyectos}

Responde SOLO con un objeto JSON, sin markdown ni texto alrededor:
{{"guardar": true|false,
  "mensaje": "lo que le dices a Diego, en {idioma}, breve y natural",
  "contenido": "el texto a guardar (normalmente la captura original tal cual)",
  "tipo": "nota|link|tarea",
  "tags": [],
  "subjects": [],
  "proyecto": "nombre del proyecto al que va, de la lista; vacío si no aplica",
  "contexto": "una línea de contexto si la conversación la aportó, si no vacío"}}"""


def _subjects(root) -> str:
    rutas = [f"{g['nombre']}/{h['nombre']}"
             for g in memoria.arbol_subjects(root) for h in g["hijos"]]
    return "\n".join(f"- {r}" for r in rutas[:MAX_SUBJECTS]) or "(todavía no hay subjects)"


def _proyectos(root, activo: str) -> str:
    """Bloque de proyectos del prompt. Solo se aceptan nombres de la lista: si
    Diego nombra uno que no existe, el agente pide crearlo — inventar proyectos
    desde el LLM ensucia la base sin que nadie lo haya decidido."""
    lista = memoria.proyectos_listar(root)
    if not lista:
        return "Diego todavía no tiene proyectos."
    partes = ["Proyectos de Diego (usa el nombre exacto; si nombra uno que no está, "
              "dile que lo cree desde el selector de proyecto):"]
    partes += [f"- {p['nombre']} ({p['ambito']})" for p in lista]
    if activo:
        partes.append(f"Proyecto activo ahora: {activo}. Si la captura es de este proyecto, "
                      "déjalo; si Diego dice que va a otro, ponlo en 'proyecto'.")
    return "\n".join(partes)


def _json_de(texto: str) -> dict:
    """Extrae el objeto JSON de la respuesta. Los modelos locales suelen
    envolverlo en ```json o agregarle una frase; se busca el primer {...}."""
    try:
        return json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"\{.*\}", texto or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def triar(cfg: dict, mensajes: list[dict], idioma: str = "es", adjunto: str = "",
          proyecto: str = "") -> dict:
    """`mensajes` = [{rol: "diego"|"agente", texto}], empezando por la captura.
    Devuelve {mensaje, guardar, payload, agente, proyecto}."""
    captura = next((m["texto"] for m in mensajes if m.get("rol") == "diego"), "")
    ag = agentes.para(cfg, "chat")
    cliente = llm_mod.crear(ag)

    system = PROMPT.format(subjects=_subjects(cfg["hamuq"]),
                           proyectos=_proyectos(cfg["hamuq"], proyecto),
                           idioma="inglés" if idioma == "en" else "español")
    if ag.get("system_prompt"):
        system = f"{ag['system_prompt']}\n\n---\n\n{system}"

    conv = [{"role": "user" if m.get("rol") == "diego" else "assistant", "content": m.get("texto", "")}
            for m in mensajes[-MAX_HISTORIAL:] if m.get("texto")]
    if adjunto:
        conv.insert(0, {"role": "user", "content": f"(adjunto: {adjunto})"})

    r = cliente.completar(system, conv, None)
    d = _json_de(r["texto"])

    # Sin JSON parseable no inventamos una decisión: se guarda tal cual, que es
    # el default seguro de MeM (cero pérdida). ponytail: un reintento si molesta.
    if not d:
        return {"mensaje": r["texto"].strip() or "Guardado.", "guardar": True,
                "payload": _payload(captura, {}, adjunto, proyecto), "agente": _ficha(ag),
                "proyecto": proyecto}

    # el proyecto que nombró Diego solo vale si existe: nada de crear proyectos
    # porque el modelo escribió un nombre parecido
    elegido = memoria.proyecto_por_nombre(cfg["hamuq"], str(d.get("proyecto") or ""))
    proyecto = elegido["nombre"] if elegido else proyecto
    return {"mensaje": str(d.get("mensaje") or "").strip(),
            "guardar": bool(d.get("guardar")),
            "payload": _payload(captura, d, adjunto, proyecto) if d.get("guardar") else None,
            "agente": _ficha(ag), "proyecto": proyecto}


def _payload(captura: str, d: dict, adjunto: str, proyecto: str = "") -> dict:
    lista = lambda k: [str(x) for x in (d.get(k) or []) if str(x).strip()]
    subjects = lista("subjects")
    if proyecto:
        subjects = list(dict.fromkeys([*subjects, memoria.subject_proyecto(proyecto)]))
    return {"contenido": str(d.get("contenido") or captura),
            "tipo": str(d.get("tipo") or "nota"),
            "contexto": str(d.get("contexto") or ""),
            "tags": lista("tags"), "subjects": subjects,
            # el proyecto que eligió el agente manda sobre el activo del chatbox
            # ("guardá esto en X"): el campo lo lee procesar.py al promover
            "proyecto": proyecto, "adjunto": adjunto, "origen": "app"}


def _ficha(ag: dict) -> dict:
    return {"proveedor": ag.get("proveedor", ""), "modelo": ag.get("modelo", "")}


# ---------------------------------------------------- catalogar un texto

PROMPT_EVAL = """Eres el catalogador rápido de MeM, la memoria personal de Diego.
Lee el texto y devuelve un título corto, tags y los subjects que le correspondan.

Subjects existentes (usa los que apliquen; propone uno nuevo solo si ninguno encaja):
{subjects}

Tags que ya se usan en la base. REUTILIZA los que apliquen, tal cual están escritos
(mismas mayúsculas y misma forma); inventa uno nuevo solo si ninguno sirve:
{tags}

Responde SOLO con un objeto JSON, sin markdown ni texto alrededor:
{{"titulo": "≤ 8 palabras", "tags": [], "subjects": []}}"""


def evaluar(cfg: dict, texto: str) -> dict:
    """Pase rápido (agente `procesar`) para titular y etiquetar un texto.
    Si el modelo falla devuelve {} — grabar nunca depende de que el LLM conteste."""
    try:
        ag = agentes.para(cfg, "procesar")
        vocabulario = ", ".join(memoria.tags_existentes(cfg["hamuq"])) or "(todavía no hay tags)"
        r = llm_mod.crear(ag).completar(PROMPT_EVAL.format(subjects=_subjects(cfg["hamuq"]), tags=vocabulario),
                                        [{"role": "user", "content": texto[:8000]}], None)
        d = _json_de(r["texto"])
    except Exception:
        return {}
    lista = lambda k: [str(x).strip() for x in (d.get(k) or []) if str(x).strip()]
    return {"titulo": str(d.get("titulo") or "").strip(),
            "tags": lista("tags"), "subjects": lista("subjects")} if d else {}


# grabar() ("⌸ Guardar" del Main: temporal → sesión, o → inbox) se borró el
# 2026-08-06 con su endpoint /sessions/{sid}/keep: el botón ya no existe (ahora
# el primer turno promueve la temporal solo, chat.turno) y nadie la llamaba.


def demo():
    """Autocheck sin red: solo la extracción de JSON, que es la parte frágil."""
    assert _json_de('{"guardar": true}') == {"guardar": True}
    assert _json_de('```json\n{"guardar": false, "mensaje": "¿tags?"}\n```')["mensaje"] == "¿tags?"
    assert _json_de("Claro:\n{\"guardar\": true, \"tags\": [\"a\"]}\n¿ok?")["tags"] == ["a"]
    assert _json_de("sin json aquí") == {}
    assert _payload("hola", {}, "")["contenido"] == "hola"
    assert _payload("hola", {"tags": ["x", "", 3]}, "")["tags"] == ["x", "3"]
    assert _payload("hola", {"subjects": ["Casa"]}, "", "Obra")["subjects"] == ["Casa", "Proyectos/Obra"]
    print("triaje ok")


if __name__ == "__main__":
    demo()
