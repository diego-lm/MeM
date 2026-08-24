"""Limpieza de la memoria: chequeos de consistencia. Reporta; no borra nada.

Dos capas: los chequeos deterministas de `correr()` (baratos, corren en cada
GET /lint) y los avisos semánticos, que salen de un LLM —de la ingesta
(procesar._contrastar) o del pase `semantico()` bajo demanda— y viven en un JSON
porque no se pueden recalcular gratis. El LLM propone; lo que Diego acepta cae
como línea fechada en el Markdown (memoria.anotar_registro), nunca al revés.
"""
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import frontmatter

from . import agentes
from . import llm as llm_mod
from . import memoria
from .triaje import _json_de

RX_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+\.md)\)")
RX_PALABRA = re.compile(r"\w{4,}")
# Confirmación léxica de los "posibles duplicados": el coseno solo elige
# candidatos. multilingual-e5-small comprime tanto el rango (ver indice.py) que
# su valor absoluto no separa "casi idéntica" de "del mismo tema" — medido sobre
# la base de Diego, la nube de pares vive en 0,82±0,03 y el 0,92 del umbral cae
# a 3σ, donde ya entran vecinas legítimas (una idea nueva que CITA a TRELLIS.2
# salía como duplicado de la ficha de TRELLIS.2). Casi idénticas comparten las
# palabras, no solo el tema: los falsos positivos reales dieron 0,13 y 0,30 de
# Jaccard, y una copia de verdad da ~1.
MIN_SOLAPE = 0.5
IGNORAR = ("10_Sesiones/", "05_Archivo/", "07_Inbox/_procesado/", "07_Inbox/sesion-",
           "06_Biblioteca_Conocimiento/Entradas/_versiones/")  # copias: sus links relativos ya no resuelven

# Descartes explícitos de Diego ("no es un duplicado de verdad" / "ese medio es a
# propósito"), compartidos entre dispositivos vía el vault — es criterio del
# usuario, no un derivado que se pueda recalcular, así que NO va a _derivados/.
IGNORAR_JSON = "09_Sistema/lint_ignorar.json"
TIPOS_IGNORABLES = ("duplicados", "medios", "avisos")


def solape(a: str, b: str) -> float:
    """Jaccard de las palabras de dos textos (≥4 letras: las cortas son relleno)."""
    A, B = set(RX_PALABRA.findall(a.lower())), set(RX_PALABRA.findall(b.lower()))
    return len(A & B) / len(A | B) if A | B else 0.0


def _ignorados(root: Path) -> dict:
    p = root / IGNORAR_JSON
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        d = {}
    return {k: list(d.get(k) or []) for k in TIPOS_IGNORABLES}


def ignorar(root: Path, tipo: str, clave: str) -> None:
    """Descarta un aviso de por vida (hasta que alguien lo saque a mano del JSON).
    `tipo` = "duplicados" | "medios" | "avisos"; `clave` = "slugA|slugB", la ruta
    del medio o la clave del aviso semántico."""
    p = root / IGNORAR_JSON
    d = _ignorados(root)
    if clave not in d[tipo]:
        d[tipo].append(clave)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------- avisos semánticos
# Salen de un LLM, así que no son recalculables en cada `correr()`: se persisten.
# Van a 09_Sistema/ y no a _derivados/ por lo mismo que lint_ignorar.json — el
# aviso vive hasta que Diego lo atienda o lo descarte, y eso es criterio suyo.
AVISOS_JSON = "09_Sistema/avisos_semanticos.json"
TIPOS_AVISO = ("contradiccion", "obsoleta", "falta_pagina", "hueco")
FRASE_AVISO = {"contradiccion": "posible contradicción", "obsoleta": "posible dato obsoleto",
               "falta_pagina": "concepto sin página propia", "hueco": "hueco de información"}


def clave_aviso(tipo: str, slugs: list[str]) -> str:
    """Identidad de un aviso. Ordenada: el mismo par en el otro sentido es el
    mismo aviso, y así el pase semántico no revive lo que ya dijo la ingesta."""
    return f"{tipo}|{'+'.join(sorted(slugs))}"


def avisos_cargar(root: Path) -> list[dict]:
    p = Path(root) / AVISOS_JSON
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:
        d = []
    return [a for a in d if isinstance(a, dict) and a.get("clave")]


def _avisos_escribir(root: Path, avisos: list[dict]) -> None:
    p = Path(root) / AVISOS_JSON
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(avisos, ensure_ascii=False, indent=2), encoding="utf-8")


def aviso_agregar(root: Path, tipo: str, slugs: list[str], detalle: str, origen: str) -> bool:
    """Registra un hallazgo del LLM. `slugs[0]` es la memoria AFECTADA (donde se
    anotará si Diego acepta); el resto, las que la contradicen o actualizan.
    Devuelve False si ya estaba o si Diego lo descartó antes."""
    if tipo not in TIPOS_AVISO or not str(detalle).strip():
        return False
    root = Path(root)
    clave = clave_aviso(tipo, list(slugs))
    if clave in _ignorados(root)["avisos"]:
        return False
    avisos = avisos_cargar(root)
    if any(a["clave"] == clave for a in avisos):
        return False
    avisos.append({"clave": clave, "tipo": tipo, "fecha": date.today().isoformat(),
                   "origen": origen, "slugs": list(slugs), "detalle": str(detalle).strip()})
    _avisos_escribir(root, avisos)
    return True


def aviso_quitar(root: Path, clave: str) -> dict | None:
    """Saca un aviso de la lista y lo devuelve (o None si no estaba)."""
    root = Path(root)
    avisos = avisos_cargar(root)
    hallado = next((a for a in avisos if a["clave"] == clave), None)
    if hallado:
        _avisos_escribir(root, [a for a in avisos if a["clave"] != clave])
    return hallado


# ---------------------------------------------------- pase semántico (LLM)
PROMPT_SEMANTICO = """Eres el auditor de la biblioteca de MeM, la memoria personal de Diego.
Abajo está la lista completa de sus memorias (slug — título — resumen).
Tu trabajo es encontrar problemas REALES y verificables entre ellas.

Devuelve SOLO un JSON (sin markdown, sin explicación):
{"avisos": [{"tipo": "contradiccion|obsoleta|falta_pagina|hueco", "slugs": ["..."], "detalle": "..."}]}

- contradiccion: dos memorias afirman datos incompatibles. slugs = [la afectada, la que la contradice].
- obsoleta: una memoria quedó superada por otra más nueva. slugs = [la superada, la que la supera].
- falta_pagina: un concepto que varias memorias mencionan y no tiene página propia. slugs = las que lo mencionan.
- hueco: información que falta y valdría la pena buscar. slugs = [] o las memorias relacionadas.

Reglas: usa los slugs TAL CUAL se te dieron. detalle = una línea en español, concreta,
citando el dato en conflicto. Si no hay nada sólido que reportar, devuelve {"avisos": []};
inventar avisos es peor que no encontrar ninguno."""


def _fichas(root: Path) -> list[str]:
    """Una línea por memoria para el auditor. Las síntesis quedan fuera: son
    derivadas, y un choque entre una síntesis y su fuente no es un problema."""
    out = []
    for p in sorted((root / memoria.ENTRADAS).glob("*.md")):
        post = frontmatter.load(p)
        if post.metadata.get("sintesis_de"):
            continue
        out.append(f"- {p.stem} — {post.metadata.get('titulo', p.stem)} — "
                   f"{memoria.resumen_corto(post.content, 240)}")
    return out


def semantico(cfg: dict) -> list[dict]:
    """Pase LLM sobre toda la Biblioteca, bajo demanda. Guarda lo que encuentra
    como avisos (que `correr` ya muestra) y devuelve los nuevos. Idempotente:
    correrlo dos veces no duplica, y lo que Diego descartó no revive.

    ponytail: una sola llamada con toda la base (58 entradas hoy); trocear por
    subject cuando el listado no entre en el contexto del modelo.
    """
    root = Path(cfg["hamuq"])
    fichas = _fichas(root)
    if len(fichas) < 2:
        return []
    cliente = llm_mod.crear(agentes.para(cfg, "procesar"))
    r = cliente.completar(PROMPT_SEMANTICO, [{"role": "user", "content": "\n".join(fichas)}], None)
    vivos = {p.stem for p in (root / memoria.ENTRADAS).glob("*.md")}
    nuevos = []
    for a in _json_de(r["texto"]).get("avisos") or []:
        tipo = str(a.get("tipo") or "")
        slugs = [s for s in (a.get("slugs") or []) if s in vivos]   # el modelo inventa slugs
        if tipo in ("contradiccion", "obsoleta") and not slugs:
            continue   # sin memoria afectada no hay dónde anotarlo: es ruido
        if aviso_agregar(root, tipo, slugs, a.get("detalle") or "", "lint"):
            nuevos.append({"tipo": tipo, "slugs": slugs, "detalle": a.get("detalle")})
    memoria.log_evento(root, "lint", f"análisis semántico: {len(nuevos)} avisos nuevos")
    return nuevos


def correr(root: Path) -> list[str]:
    root = Path(root)
    problemas = []

    # 1. links .md rotos en índices y fichas
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(IGNORAR) or "_papelera/" in rel or "_PLANTILLA" in rel:
            continue
        texto = p.read_text(encoding="utf-8", errors="replace")
        texto = re.sub(r"<!--.*?-->", "", texto, flags=re.S)  # los comentarios traen links de ejemplo
        for m in RX_LINK.finditer(texto):
            destino = m.group(1)
            if destino.startswith(("http", "mailto")):
                continue
            if not (p.parent / destino).exists():
                problemas.append(f"link roto: {rel} → {destino}")

    # 2. entradas huérfanas (sin link entrante desde el índice temático)
    idx = root / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md"
    indice = idx.read_text(encoding="utf-8") if idx.exists() else ""
    entradas = root / "06_Biblioteca_Conocimiento/Entradas"
    if entradas.exists():
        for p in sorted(entradas.glob("*.md")):
            if p.name not in indice:
                problemas.append(f"entrada huérfana (falta en 00_INDICE_TEMATICO.md): Entradas/{p.name}")

    ignorados = _ignorados(root)

    # 3. medios: los dos lados del vínculo entrada ↔ archivo
    # (una entrada que apunta a un png borrado se ve como imagen rota en la app;
    #  un archivo que nadie referencia es un medio generado que se perdió del catálogo)
    referidos = memoria.adjuntos_referidos(root)
    for adj, paginas in sorted(referidos.items()):
        if not (root / adj).is_file():
            problemas += [f"adjunto inexistente: {rel} → {adj}" for rel in paginas]
    for p in sorted((root / "07_Inbox/_adjuntos").glob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and rel not in referidos and rel not in ignorados["medios"]:
            problemas.append(f"medio sin entrada (nada lo referencia): {rel}")

    # 4. [[wikilinks]] rotos y memorias casi duplicadas (embeddings del índice
    # derivado; sin índice o sin vectores el chequeo semántico calla y el resto corre)
    try:
        from . import indice
        vivos = {p.stem for p in entradas.glob("*.md")} if entradas.exists() else set()
        for p in sorted(entradas.glob("*.md")) if vivos else []:
            cuerpo = re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8", errors="replace"), flags=re.S)
            for destino in sorted(set(indice.RX_WIKILINK.findall(cuerpo))):
                if destino not in vivos:
                    problemas.append(f"wikilink roto: Entradas/{p.name} → [[{destino}]]")
        for a, b, s in indice.duplicados(root):
            if f"{a}|{b}" in ignorados["duplicados"] or f"{b}|{a}" in ignorados["duplicados"]:
                continue
            pa = frontmatter.load(entradas / f"{a}.md")
            pb = frontmatter.load(entradas / f"{b}.md")
            ma, mb = pa.metadata, pb.metadata
            if ma.get("origen") == "crear" and mb.get("origen") == "crear":
                continue   # variantes esperadas del mismo prompt en el taller de medios, no un duplicado real
            if ma.get("sintesis_de") or mb.get("sintesis_de"):
                continue   # una síntesis SE PARECE a sus fuentes: es su trabajo, no un duplicado
            if solape(pa.content, pb.content) < MIN_SOLAPE:
                continue   # mismo tema, otra cosa: el coseno propone, las palabras confirman
            fa = ma.get("creada") or ma.get("actualizada") or "?"
            fb = mb.get("creada") or mb.get("actualizada") or "?"
            problemas.append(f"posible duplicado (similitud {s:.2f}, {fa} ≈ {fb}): Entradas/{a}.md ≈ Entradas/{b}.md")
    except Exception:
        pass

    # 5. sesiones activas abandonadas (>30 días sin actividad)
    ses = root / "10_Sesiones"
    if ses.exists():
        for p in sorted(ses.glob("*.md")):
            meta = frontmatter.load(p).metadata
            act = str(meta.get("actualizada", ""))[:19]
            try:
                vieja = datetime.now() - datetime.fromisoformat(act) > timedelta(days=30)
            except ValueError:
                vieja = False
            if meta.get("estado") == "activa" and vieja:
                problemas.append(f"sesión activa sin uso >30 días: 10_Sesiones/{p.name} (considerar archivar)")

    # 6. avisos semánticos ya guardados (los produjo un LLM en otro momento;
    # acá solo se leen, para que este chequeo siga costando lo mismo que antes)
    for a in avisos_cargar(root):
        if a["clave"] in ignorados["avisos"]:
            continue
        refs = " ↔ ".join(f"Entradas/{s}.md" for s in a.get("slugs") or [])
        problemas.append(f"aviso semántico [{a['clave']}]: {FRASE_AVISO.get(a['tipo'], 'aviso')}"
                         f"{' en ' + refs if refs else ''} — {a['detalle']}")

    return problemas
