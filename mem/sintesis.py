"""Páginas de síntesis por tema: lo que la biblioteca SABE de un subject, no lo
que capturó. Es la pieza del patrón "LLM Wiki" (Karpathy) que a MeM le faltaba —
sin esto cada memoria es una isla y el conocimiento no compone.

Tres decisiones de diseño:

  · Una síntesis es una entrada normal con `sintesis_de: <subject>` en el
    frontmatter. Así hereda gratis versionado, registro histórico, papelera,
    índices, búsqueda, grafo y ficha; y el dedupe por slug de guardar_entrada
    convierte "regenerar" en "actualizar la misma página", con la anterior
    guardada en _versiones/.

  · Es DERIVADA, no fuente: se puede borrar y no se pierde nada, porque las
    memorias que la alimentan quedan intactas. Por eso el lint no la compara con
    sus fuentes (se le parece por definición) y el contraste de la ingesta la
    saltea.

  · Se crea a mano (Diego elige qué tema merece página viva) y se refresca sola
    cuando entra material bajo ese tema. Nunca al revés: refrescar no inventa
    síntesis nuevas.
"""
from pathlib import Path

import frontmatter

from . import agentes
from . import llm as llm_mod
from . import memoria

MAX_FUENTES = 40       # ponytail: tope de memorias por síntesis; trocear si un tema crece más
MAX_RESUMEN = 1500     # chars de cada fuente que ve el modelo

PROMPT = """Eres el bibliotecario de MeM, la memoria personal de Diego.
Abajo están todas sus memorias sobre el tema "{subject}".

Escribe la página de síntesis del tema: lo que la biblioteca SABE, no una lista
de lo que guardó. En español, en Markdown, sin título de nivel 1.

- Integra: agrupa por sub-tema, conecta lo que se relaciona, no repitas.
- Conserva los datos concretos (nombres, cifras, fechas, decisiones, conclusiones).
- Si dos memorias se contradicen, dilo explícitamente en vez de elegir una.
- Si algo evolucionó en el tiempo, cuenta la evolución.
- Nada de relleno ni de meta-comentarios sobre la tarea.

Devuelve SOLO el texto de la síntesis."""


def slug_sintesis(subject: str) -> str:
    """Determinista y ajeno al título: regenerar siempre escribe la misma página."""
    return memoria.slugificar(f"Sintesis {memoria.normalizar_subject(subject)}")


def fuentes_de(root: Path, subject: str) -> list[dict]:
    """Memorias bajo el subject (match por segmento, como buscar_memorias),
    EXCLUYENDO las síntesis: una síntesis no se alimenta de otra, ni siquiera de
    la de su subject padre."""
    root = Path(root)
    entradas = root / memoria.ENTRADAS
    if not entradas.exists():
        return []
    out = []
    for p in sorted(entradas.glob("*.md")):
        post = frontmatter.load(p)
        m = post.metadata
        if m.get("sintesis_de"):
            continue
        if not any(memoria._bajo(s, subject) for s in (m.get("subjects") or [])):
            continue
        out.append({"slug": p.stem, "titulo": str(m.get("titulo", p.stem)),
                    "actualizada": str(m.get("actualizada") or m.get("creada") or ""),
                    "resumen": memoria.resumen_corto(post.content, MAX_RESUMEN)})
    return out[:MAX_FUENTES]


def sintetizar(cfg: dict, subject: str) -> dict:
    """Crea o regenera la página de síntesis de un tema. Una llamada al LLM."""
    root = Path(cfg["hamuq"])
    subject = memoria.normalizar_subject(subject)
    fuentes = fuentes_de(root, subject)
    if not fuentes:
        raise ValueError(f"no hay memorias bajo el tema '{subject}'")
    cliente = llm_mod.crear(agentes.para(cfg, "procesar"))
    listado = "\n\n".join(f"## {f['titulo']} ({f['slug']}, {f['actualizada']})\n{f['resumen']}"
                          for f in fuentes)
    r = cliente.completar(PROMPT.format(subject=subject),
                          [{"role": "user", "content": listado}], None)
    texto = (r["texto"] or "").strip()
    if not texto:
        raise ValueError("el modelo no devolvió síntesis")
    # los links los arma el código, no el modelo: así apuntan a archivos que
    # existen de verdad y el lint vigila que sigan resolviendo. Links markdown y
    # no [[wikilinks]] — ningún automatismo escribe wikilinks (§7.14).
    citas = ", ".join(f"[{f['titulo']}](Entradas/{f['slug']}.md)" for f in fuentes)
    slug = slug_sintesis(subject)
    resultado = memoria.guardar_entrada(
        root, f"Síntesis: {subject}", f"{texto}\n\nFuentes: {citas}", [subject],
        origen="sintesis", slug=slug, reemplazar=True,
        meta={"sintesis_de": subject, "fuentes": [f["slug"] for f in fuentes]})
    memoria.log_evento(root, "sintesis", f"{subject}: {len(fuentes)} fuentes")
    return {"slug": slug, "subject": subject, "fuentes": len(fuentes), "resultado": resultado}


def _sintesis_existentes(root: Path) -> list[tuple[str, dict]]:
    out = []
    for p in sorted((Path(root) / memoria.ENTRADAS).glob("*.md")):
        m = frontmatter.load(p).metadata
        if m.get("sintesis_de"):
            out.append((p.stem, dict(m)))
    return out


def desactualizadas(root: Path, subjects: list[str] | None = None) -> list[str]:
    """Temas cuya síntesis ya no refleja su material: apareció una memoria que no
    está en `fuentes`, o alguna fuente se actualizó después que la síntesis.
    Puro frontmatter, sin LLM."""
    root = Path(root)
    pedidos = [memoria.normalizar_subject(s) for s in (subjects or [])]
    out = []
    for _slug, m in _sintesis_existentes(root):
        sub = memoria.normalizar_subject(str(m["sintesis_de"]))
        # solo los temas tocados: una captura de "Casa" no re-sintetiza "Trabajo"
        if pedidos and not any(memoria._bajo(s, sub) for s in pedidos):
            continue
        fuentes = fuentes_de(root, sub)
        previas = set(m.get("fuentes") or [])
        cuando = str(m.get("actualizada") or "")
        if any(f["slug"] not in previas or f["actualizada"] > cuando for f in fuentes):
            out.append(sub)
    return out


def refrescar(cfg: dict, subjects: list[str] | None = None) -> dict:
    """Regenera las síntesis que quedaron atrás. NUNCA crea una síntesis nueva:
    qué tema merece página viva lo decide Diego, no el procesador de inbox."""
    root = Path(cfg["hamuq"])
    hechas, fallidas = [], []
    for sub in desactualizadas(root, subjects):
        try:
            sintetizar(cfg, sub)
            hechas.append(sub)
        except Exception as e:
            fallidas.append(sub)
            memoria.log_evento(root, "sintesis", f"{sub}: falló — {e}")
    return {"refrescadas": hechas, "fallidas": fallidas}


def demo():
    """Autocheck sin LLM: el slug es determinista y las síntesis no se comen entre sí."""
    import tempfile
    assert slug_sintesis("Tecnologia/VR") == slug_sintesis("Tecnologia > VR")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        memoria.guardar_entrada(root, "Visor nuevo", "Un visor.", ["Tecnologia/VR"])
        (root / memoria.ENTRADAS / "sintesis-tecnologia-vr.md").write_text(
            "---\ntitulo: S\nsintesis_de: Tecnologia/VR\nsubjects:\n  - Tecnologia/VR\n---\n\nx\n",
            encoding="utf-8")
        fuentes = [f["slug"] for f in fuentes_de(root, "Tecnologia")]
        assert fuentes == ["visor-nuevo"], fuentes   # la síntesis no se alimenta de sí misma
        assert desactualizadas(root) == ["Tecnologia/VR"]
    print("sintesis ok")


if __name__ == "__main__":
    demo()
