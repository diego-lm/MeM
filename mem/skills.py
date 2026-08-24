"""Skills: instrucciones en Markdown que el agente abre cuando le sirven.

Mismo patrón que modos.py — datos, no código: archivos en 09_Sistema/Skills/ con
frontmatter `descripcion`. Al system prompt solo va el índice (nombre + una
línea); el cuerpo se carga únicamente si el agente lo pide con la tool `skill`.
Así agregar una skill es escribir un .md, sin tocar nada de este repo.
"""
from pathlib import Path

import frontmatter

TOOLS = [
    {"name": "skill",
     "description": "Abre una skill: instrucciones detalladas para una tarea concreta. Usar cuando el índice de skills tenga una que aplique a lo que se está haciendo.",
     "parameters": {"type": "object", "properties": {"nombre": {"type": "string"}}, "required": ["nombre"]}},
]


def _dir(root: Path) -> Path:
    return Path(root) / "09_Sistema" / "Skills"


def listar(root: Path) -> list[dict]:
    d = _dir(root)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        meta = frontmatter.load(p).metadata
        out.append({"nombre": str(meta.get("nombre") or p.stem),
                    "descripcion": str(meta.get("descripcion") or "")})
    return out


def indice(root: Path) -> str:
    disponibles = listar(root)
    if not disponibles:
        return ""
    lineas = "\n".join(f"- {s['nombre']}: {s['descripcion']}" for s in disponibles)
    return f"Skills disponibles (ábrelas con la tool `skill` cuando apliquen):\n{lineas}"


def leer(root: Path, nombre: str) -> str:
    p = _dir(root) / f"{Path(nombre).stem}.md"
    if not p.is_file():
        return f"(no existe la skill: {nombre})"
    return frontmatter.load(p).content.strip()


def demo():
    """Autocheck: el índice y la lectura sobre una carpeta temporal."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert listar(root) == [] and indice(root) == ""
        d = _dir(root)
        d.mkdir(parents=True)
        (d / "investigar.md").write_text("---\ndescripcion: como investigar\n---\n\nPaso 1.\n", encoding="utf-8")
        assert listar(root) == [{"nombre": "investigar", "descripcion": "como investigar"}]
        assert "investigar: como investigar" in indice(root)
        assert leer(root, "investigar") == "Paso 1."
        assert leer(root, "../../etc/passwd").startswith("(no existe")
    print("skills ok")


if __name__ == "__main__":
    demo()
