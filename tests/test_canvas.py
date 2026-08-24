"""Canvas de brainstorming (canvas.py): CRUD JSON + papelera + sugerir con el
LLM monkeypatcheado (cero red). MEM_EMBED_FAKE=1 vía conftest."""
import json

import pytest

from mem import canvas, memoria


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("MEM_EMBED_FAKE", "1")
    r = tmp_path / "hamuQ"
    for d in ("09_Sistema", "06_Biblioteca_Conocimiento/Entradas", "08_Categorias", "07_Inbox"):
        (r / d).mkdir(parents=True)
    (r / "09_Sistema/log.md").write_text("# Log\n", encoding="utf-8")
    (r / "08_Categorias/00_CATEGORIAS.md").write_text("# Categorías\n\n## Tecnologia\n", encoding="utf-8")
    (r / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md").write_text(
        "# Índice Temático\n\n## Por etiqueta/categoría\n\n## Todas las entradas (orden cronológico)\n",
        encoding="utf-8")
    return r


def test_crud(root):
    c = canvas.crear(root, "Ideas raras")
    assert c["titulo"] == "Ideas raras" and c["tarjetas"] == []
    assert canvas.listar(root)[0]["id"] == c["id"]

    tarjetas = [{"id": "t1", "tipo": "texto", "texto": "hola", "x": 10, "y": 20}]
    canvas.guardar(root, c["id"], None, tarjetas)
    d = canvas.leer(root, c["id"])
    assert d["tarjetas"] == tarjetas and d["titulo"] == "Ideas raras"
    canvas.guardar(root, c["id"], "Otro nombre", None)   # patch parcial: tarjetas quedan
    d = canvas.leer(root, c["id"])
    assert d["titulo"] == "Otro nombre" and d["tarjetas"] == tarjetas

    # el archivo en disco es JSON legible (fuente de verdad del tablero)
    crudo = json.loads((root / canvas.CARPETA / f"{c['id']}.json").read_text(encoding="utf-8"))
    assert crudo["tarjetas"][0]["texto"] == "hola"


def test_eliminar_va_a_papelera(root):
    c = canvas.crear(root, "Efímero")
    ruta = canvas.eliminar(root, c["id"])
    assert "_papelera" in ruta and (root / ruta).exists()   # nunca destructivo
    with pytest.raises(FileNotFoundError):
        canvas.leer(root, c["id"])
    assert canvas.listar(root) == []


def test_id_saneado(root):
    with pytest.raises(FileNotFoundError):
        canvas.leer(root, "../../09_Sistema/log")   # path traversal desde el URL


def test_sugerir_mezcla_y_valida_fuentes(root, monkeypatch):
    memoria.guardar_entrada(root, "Captura volumétrica", "Gaussian splatting para VR.", ["Tecnologia"])
    memoria.guardar_entrada(root, "Captura volumetrica dos", "Gaussian splatting para VR avanzado.", ["Tecnologia"])
    memoria.guardar_entrada(root, "Receta de pan", "Masa madre con harina integral.", ["Cocina"])
    c = canvas.crear(root, "Brainstorm")
    cfg = {"hamuq": root, "agentes": {}}

    visto = {}
    class _Cliente:
        def completar(self, system, mensajes, _):
            visto["material"] = mensajes[0]["content"]
            return {"texto": json.dumps({"ideas": [
                {"texto": "Mezclar splatting con pan", "fuentes": ["captura-volumetrica", "inventado"]},
                {"texto": "", "fuentes": []},   # vacía: se descarta
            ]})}
    monkeypatch.setattr(canvas.agentes, "para", lambda cfg, rol: {"proveedor": "x", "modelo": "y"})
    monkeypatch.setattr(canvas.llm_mod, "crear", lambda ag: _Cliente())

    r = canvas.sugerir(cfg, c["id"], ["captura-volumetrica"])
    assert r["ideas"] == [{"texto": "Mezclar splatting con pan", "fuentes": ["captura-volumetrica"]}]
    # el material mezcla el tablero con vecinas y lejanas — las tres secciones presentes
    assert "MEMORIAS DEL TABLERO" in visto["material"] and "captura-volumetrica]" in visto["material"]
    assert "CERCANAS POR TEMA" in visto["material"] and "LEJANAS AL AZAR" in visto["material"]

    with pytest.raises(FileNotFoundError):
        canvas.sugerir(cfg, "99999999999999-ffff", [])
