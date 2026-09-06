"""Reorganización de temas de la Biblioteca (pedido 2026-08-01).

Dos fases separadas a propósito:
  planear()  — SOLO LEE. Propone un árbol de temas para toda la Biblioteca (una
               llamada al LLM con todos los títulos) y luego asigna 1–3 temas por
               entrada contra ese árbol. Devuelve el plan completo antes→después.
  aplicar()  — escribe un plan ya revisado: frontmatter + registro fechado en cada
               entrada cambiada, y reconstruye los índices desde el frontmatter.

Si el LLM falla (todo o una entrada), esa entrada conserva sus temas: cero pérdida.
"""
import sys
from datetime import date

import frontmatter

from . import agentes
from . import llm as llm_mod
from . import memoria
from .triaje import _json_de

MAX_CUERPO = 4000    # chars de cada entrada que ve el modelo
MAX_TEMAS = 3

PROMPT_ARBOL = """Eres el bibliotecario de MeM, la memoria personal de Diego.
Abajo está la lista completa de entradas de su biblioteca (título — resumen).
Diseña el árbol de temas que mejor las organice.

Reglas ESTRICTAS de formato:
- Cada tema tiene EXACTAMENTE una barra: "Grupo/Subgrupo". Ni una barra más.
- El subgrupo es UN solo concepto, de 1 a 3 palabras. PROHIBIDO pegar varios
  nombres en un subgrupo (mal: "LM Studio/Mage/Hermes", "Satore BlackTree Philly";
  bien: "LM Studio", "Clientes VR").
- En español, nombres naturales y cortos.
- Pocos grupos (4 a 8) con subgrupos concretos; evita grupos de un solo uso.
- Piensa en cómo Diego buscaría esto dentro de un año.

Responde SOLO un JSON: {"arbol": ["Grupo/Subgrupo", ...]}"""

PROMPT_ASIGNA = """Eres el bibliotecario de MeM, la memoria personal de Diego.
Asigna a la entrada los temas que le correspondan según su CONTENIDO.

Reglas ESTRICTAS:
- 1 a 3 temas, cada uno con EXACTAMENTE una barra: "Grupo/Subgrupo".
- El subgrupo es UN solo concepto (1 a 3 palabras); nunca pegues varios nombres.
- Una entrada puede pertenecer a varios temas si su contenido lo amerita.
- USA el árbol dado tal cual; propone un tema nuevo SOLO si ninguno encaja de verdad.

Árbol de temas:
{arbol}

Responde SOLO un JSON: {{"subjects": ["Grupo/Subgrupo", ...]}}"""


def _tema_limpio(s: str) -> str:
    """Clamp de formato: dos niveles exactos, pase lo que pase el modelo diga."""
    seg = [x.strip() for x in str(s).split("/") if x.strip()]
    return "/".join(seg[:2])


def _entradas(root) -> list[dict]:
    out = []
    for p in sorted((root / memoria.ENTRADAS).glob("*.md")):
        post = frontmatter.load(p)
        out.append({"slug": p.stem, "titulo": str(post.metadata.get("titulo", p.stem)),
                    "antes": [str(s) for s in (post.metadata.get("subjects") or [])],
                    "cuerpo": post.content[:MAX_CUERPO]})
    return out


def planear(cfg: dict) -> dict:
    """Calcula el plan (no escribe nada). {"arbol": [...], "cambios": [{slug,
    titulo, antes, despues}, ...]} — despues == antes cuando el LLM no contestó."""
    root = cfg["hamuq"]
    entradas = _entradas(root)
    try:
        cliente = llm_mod.crear(agentes.para(cfg, "procesar"))
    except Exception:
        cliente = None

    # semilla: los temas que ya existen en el frontmatter (por si el LLM cae)
    arbol = list(dict.fromkeys(s for e in entradas for s in e["antes"]))
    if cliente:
        try:
            listado = "\n".join(f"- {e['titulo']} — {memoria.resumen_corto(e['cuerpo'], 120)}" for e in entradas)
            r = cliente.completar(PROMPT_ARBOL, [{"role": "user", "content": listado}], None)
            propuesto = list(dict.fromkeys(
                t for x in (_json_de(r["texto"]).get("arbol") or []) if (t := _tema_limpio(x))))
            if propuesto:
                arbol = propuesto
        except Exception:
            pass

    cambios = []
    for e in entradas:
        despues = e["antes"]
        if cliente:
            try:
                r = cliente.completar(
                    PROMPT_ASIGNA.format(arbol="\n".join(f"- {a}" for a in arbol)),
                    [{"role": "user", "content": f"# {e['titulo']}\n\n{e['cuerpo']}"}], None)
                temas = [t for s in (_json_de(r["texto"]).get("subjects") or []) if (t := _tema_limpio(s))]
                if temas:
                    # el proyecto NO se reorganiza: es dónde vive la memoria, no
                    # un tema. Sin esto el bibliotecario la sacaba de su proyecto.
                    proy = [s for s in e["antes"] if memoria.proyectos_de([s])]
                    despues = [*proy, *list(dict.fromkeys(temas))[:MAX_TEMAS]]
            except Exception:
                pass
        for s in despues:
            if s not in arbol:
                arbol.append(s)  # el árbol crece: las siguientes entradas lo reusan
        cambios.append({"slug": e["slug"], "titulo": e["titulo"], "antes": e["antes"], "despues": despues})
        print(f"· {e['slug']}: {e['antes']} → {despues}", file=sys.stderr)
    return {"arbol": arbol, "cambios": cambios}


# ------------------------------------------------------------------ tags
# La base llegó a 125 tags para 45 entradas porque cada pase del catalogador los
# inventaba de cero: `IA generativa` y `ia-generativa` y `imagen IA` conviven sin
# ser el mismo filtro. Esto los unifica UNA vez (de ahí en más el vocabulario va
# en el prompt, triaje.PROMPT_EVAL) y barre los de las pruebas.
BASURA = {"fijo-test", "verificado-e2e", "corregido"}


def _clave(tag: str) -> str:
    """Dos tags son el mismo si solo difieren en mayúsculas, tildes o separador."""
    return memoria._norm(tag).replace("-", " ").replace("_", " ").strip()


def plan_tags(root) -> dict[str, str]:
    """{tag actual: forma canónica} — canónica = la variante más usada; a igual
    uso gana la minúscula (es la convención del resto de la base y la que mejor
    se lee en una pastilla) y después la más corta. Lo que ya está bien no sale."""
    variantes: dict[str, dict[str, int]] = {}
    for p in sorted((root / memoria.ENTRADAS).glob("*.md")):
        for t in frontmatter.load(p).metadata.get("tags") or []:
            if s := str(t).strip():
                variantes.setdefault(_clave(s), {})[s] = variantes.get(_clave(s), {}).get(s, 0) + 1
    mapa = {}
    for formas in variantes.values():
        mejor = sorted(formas.items(),
                       key=lambda kv: (-kv[1], kv[0] != kv[0].lower(), len(kv[0]), kv[0]))[0][0]
        mapa.update({f: ("" if f in BASURA else mejor) for f in formas if f != mejor or f in BASURA})
    return mapa


def aplicar_tags(cfg: dict, mapa: dict[str, str]) -> dict:
    """Reescribe los tags de la Biblioteca según el mapa ("" = borrar el tag)."""
    root = cfg["hamuq"]
    n = 0
    for p in sorted((root / memoria.ENTRADAS).glob("*.md")):
        post = frontmatter.load(p)
        antes = [str(t).strip() for t in (post.metadata.get("tags") or []) if str(t).strip()]
        despues = list(dict.fromkeys(x for t in antes if (x := mapa.get(t, t))))
        if despues == antes:
            continue
        # sin versionar y sin registro histórico: cambiar "IA generativa" por
        # "ia-generativa" no es una edición de contenido, es ortografía del índice
        post.metadata["tags"] = despues
        p.write_text(frontmatter.dumps(post), encoding="utf-8")
        n += 1
    resultado = memoria.reconstruir_indices(root)
    memoria.log_evento(root, "tags", f"{n} entradas normalizadas; {resultado}")
    return {"cambiadas": n, "indices": resultado}


def aplicar(cfg: dict, plan: dict) -> dict:
    """Escribe un plan: subjects + registro fechado por entrada cambiada, y
    reconstruye los índices. Nada se borra (los índices previos van a papelera)."""
    root = cfg["hamuq"]
    hoy = date.today().isoformat()
    n = 0
    for c in plan["cambios"]:
        if list(c["despues"]) == list(c["antes"]):
            continue
        p = root / memoria.ENTRADAS / f"{c['slug']}.md"
        if not p.is_file():
            continue
        post = frontmatter.load(p)
        post.metadata["subjects"] = list(c["despues"])
        post.metadata["actualizada"] = hoy
        post.content += (f"\n**{hoy}** (reorganización) — temas: "
                         f"{', '.join(c['antes']) or '(ninguno)'} → {', '.join(c['despues'])}.\n")
        p.write_text(frontmatter.dumps(post), encoding="utf-8")
        n += 1
    resultado = memoria.reconstruir_indices(root)
    memoria.log_evento(root, "reorganizar", f"{n} entradas recategorizadas; {resultado}")
    return {"cambiadas": n, "indices": resultado}
