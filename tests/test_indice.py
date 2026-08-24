"""Índice derivado de búsqueda (indice.py + embed.py) sobre un vault de fixture.

MEM_EMBED_FAKE=1 en todo el módulo: embeddings deterministas por bolsa de
palabras hasheadas — cero red, cero descarga de modelo.
"""
import re
import urllib.request
from pathlib import Path

import pytest

from mem import embed, indice, memoria


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


def _db(root: Path) -> Path:
    return root / indice.DERIVADOS / "mem.db"


def test_hook_indexa_y_borra(root):
    memoria.guardar_entrada(root, "Técnica de captura volumétrica",
                            "Gaussian splatting para VR en tiempo real.", ["Tecnologia/Inmersivo"],
                            tags=["vr"])
    assert _db(root).exists()   # el hook de guardar_entrada creó y pobló el índice
    hits = indice.buscar_hibrida(root, "tecnica volumetrica")   # sin tildes: remove_diacritics
    assert hits and hits[0][0] == "tecnica-de-captura-volumetrica"
    memoria.eliminar_entrada(root, "tecnica-de-captura-volumetrica")
    assert indice.buscar_hibrida(root, "tecnica volumetrica") == []


def test_hibrida_rankea_por_ambas_ramas(root):
    memoria.guardar_entrada(root, "Cohete de agua", "Un cohete propulsado con agua y presión.", ["Ciencia"])
    memoria.guardar_entrada(root, "Receta de pan", "Pan de masa madre con harina integral.", ["Cocina"])
    hits = indice.buscar_hibrida(root, "cohete propulsado con agua")
    assert hits[0][0] == "cohete-de-agua"
    # la rama léxica encuentra dentro de la transcripción del adjunto (no embebida)
    memoria.guardar_entrada(root, "Foto del taller", "Una foto del taller.", ["Trabajo"],
                            transcripcion="En la pizarra se lee: prototipo alfa listo el martes")
    hits = indice.buscar_hibrida(root, "prototipo alfa pizarra")
    assert hits[0][0] == "foto-del-taller"


def test_sincronizar_absorbe_ediciones_externas(root):
    memoria.guardar_entrada(root, "Nota original", "Contenido inicial cualquiera.", ["Tecnologia"])
    p = root / memoria.ENTRADAS / "nota-original.md"
    # edición por fuera de memoria.py (Obsidian, Dropbox): la detecta el diff de mtimes
    p.write_text(p.read_text(encoding="utf-8") + "\nfrase agregada externamente zanahoria\n",
                 encoding="utf-8")
    hits = indice.buscar_hibrida(root, "zanahoria")
    assert hits and hits[0][0] == "nota-original"


def test_corrupcion_se_regenera_sola(root):
    memoria.guardar_entrada(root, "Entrada estable", "Texto persistente sobre alpacas.", ["Vida"])
    _db(root).write_bytes(b"esto no es una base sqlite")
    hits = indice.buscar_hibrida(root, "alpacas")   # abrir() tira el archivo y sincroniza
    assert hits and hits[0][0] == "entrada-estable"


def test_vecinas_por_coseno(root):
    memoria.guardar_entrada(root, "Viaje a Cusco", "Caminata por las montañas y ruinas de piedra.", ["Viajes"])
    memoria.guardar_entrada(root, "Trekking en Huaraz", "Caminata por las montañas y lagunas de altura.", ["Viajes"])
    memoria.guardar_entrada(root, "Factura de luz", "Pago mensual del servicio eléctrico.", ["Casa"])
    vs = indice.vecinas(root, "viaje-a-cusco", umbral=0.3)
    assert vs and vs[0][0] == "trekking-en-huaraz"
    assert all(s != "factura-de-luz" for s, _ in vs)


def test_wikilinks_y_backlinks(root):
    memoria.guardar_entrada(root, "Nota A", "Relacionada con [[nota-b]] por diseño.", ["Tecnologia"])
    memoria.guardar_entrada(root, "Nota B", "La base del asunto.", ["Tecnologia"])
    salientes, backlinks = indice.wikilinks_de(root, "nota-a")
    assert salientes == ["nota-b"] and backlinks == []
    salientes, backlinks = indice.wikilinks_de(root, "nota-b")
    assert salientes == [] and backlinks == ["nota-a"]
    # sintaxis [[slug|texto visible]]
    memoria.guardar_entrada(root, "Nota C", "Ver [[nota-a|la primera nota]].", ["Tecnologia"])
    assert indice.wikilinks_de(root, "nota-c")[0] == ["nota-a"]


def test_duplicados_por_coseno(root):
    texto = ("Resumen de la reunión con el cliente sobre el museo virtual: "
             "alcance, fechas, presupuesto y los tres entregables acordados para diciembre.")
    memoria.guardar_entrada(root, "Reunión museo", texto, ["Trabajo"])
    memoria.guardar_entrada(root, "Reunión museo bis", texto, ["Trabajo"])
    memoria.guardar_entrada(root, "Otra cosa", "Lista de compras del supermercado.", ["Casa"])
    pares = indice.duplicados(root, umbral=0.9)
    assert len(pares) == 1
    assert {pares[0][0], pares[0][1]} == {"reunion-museo", "reunion-museo-bis"}


def test_proyeccion_2d(root):
    for i, tema in enumerate(["montañas y ríos", "código y compiladores", "recetas y cocina"]):
        memoria.guardar_entrada(root, f"Nota {i}", f"Apuntes sobre {tema}.", ["Tecnologia"])
    puntos = indice.proyeccion(root)
    assert len(puntos) == 3
    assert all(isinstance(p["x"], float) and isinstance(p["y"], float) for p in puntos)
    assert puntos[0]["grupo"] == "Tecnologia"
    # cacheada: una segunda llamada devuelve lo mismo sin recalcular
    assert indice.proyeccion(root) == puntos


class _Resp:
    def __init__(self, cuerpo: bytes):
        self.cuerpo = cuerpo

    def read(self):
        return self.cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_mapa_geocodifica_una_sola_vez(root, monkeypatch):
    memoria.guardar_entrada(root, "Almuerzo en Lima", "Ceviche.", ["Vida"], meta={"lugar": "Lima"})
    memoria.guardar_entrada(root, "Otra en Lima", "Más ceviche.", ["Vida"], meta={"lugar": "Lima"})
    memoria.guardar_entrada(root, "Sala 3", "Reunión interna.", ["Trabajo"], meta={"lugar": "Sala 3"})
    llamadas = []

    def fake_urlopen(req, timeout=0):
        llamadas.append(req.full_url)
        cuerpo = b'[{"lat": "-12.04", "lon": "-77.03"}]' if "Lima" in req.full_url else b"[]"
        return _Resp(cuerpo)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(indice.time, "sleep", lambda s: None)   # sin esperar en tests
    r = indice.mapa(root)
    assert len(llamadas) == 2   # dos lugares distintos, una consulta cada uno
    assert r["lugares"] == [{"lugar": "Lima", "lat": -12.04, "lon": -77.03, "n": 2,
                             "memorias": [{"slug": "almuerzo-en-lima", "titulo": "Almuerzo en Lima"},
                                          {"slug": "otra-en-lima", "titulo": "Otra en Lima"}]}]
    assert r["sin_geo"] == ["Sala 3"]

    # segunda pasada: TODO sale de la geocache — ni el irresoluble se reintenta
    def sin_red(req, timeout=0):
        raise AssertionError("no debería salir a la red: la geocache manda")

    monkeypatch.setattr(urllib.request, "urlopen", sin_red)
    assert indice.mapa(root) == r


def test_lugar_de_captura(root, monkeypatch):
    """Las coordenadas del browser viajan de la captura a la memoria, y Nominatim
    inverso les pone un nombre que un humano entiende."""
    import frontmatter

    ruta = memoria.capturar(root, "Idea suelta en el café", coords="-12.121000,-77.030000")
    item = frontmatter.load(root / ruta).metadata
    assert item["coords_captura"] == "-12.121000,-77.030000"
    # sin coords la clave no existe: no se ensucia el frontmatter de lo que no la tiene
    assert "coords_captura" not in frontmatter.load(
        root / memoria.capturar(root, "Otra idea sin geo")).metadata

    def fake_urlopen(req, timeout=0):
        assert "/reverse?" in req.full_url and "lat=-12.121" in req.full_url
        return _Resp(b'{"address": {"suburb": "Miraflores", "city": "Lima", "country": "Per\\u00fa"}}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert indice.lugar_de_coords("-12.121000,-77.030000") == "Miraflores, Lima, Perú"
    assert indice.lugar_de_coords("") == "" and indice.lugar_de_coords("basura") == ""

    # el `extra` que arma procesar_item llega a la entrada y de ahí a la ficha
    extra = {"coords_captura": item["coords_captura"],
             "lugar_captura": indice.lugar_de_coords(item["coords_captura"])}
    memoria.guardar_entrada(root, "Idea del café", "Una idea.", ["Vida"], meta=extra)
    ficha = [m for m in memoria.buscar_memorias(root) if m["slug"] == "idea-del-cafe"][0]
    assert ficha["lugar_captura"] == "Miraflores, Lima, Perú"
    assert ficha["coords_captura"] == "-12.121000,-77.030000"
    # sin red la memoria conserva las coordenadas: el nombre es un lujo, el punto no
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("sin red")))
    assert indice.lugar_de_coords("-12.121,-77.030") == ""


def test_hook_roto_no_rompe_la_escritura(root, monkeypatch):
    monkeypatch.setattr(indice, "sincronizar", lambda *a, **k: 1 / 0)
    out = memoria.guardar_entrada(root, "Escritura protegida", "El vault manda.", ["Vida"])
    assert "creada" in out
    assert (root / memoria.ENTRADAS / "escritura-protegida.md").exists()


def test_reindexar_da_stats(root):
    memoria.guardar_entrada(root, "Uno", "Primera nota.", ["Vida"])
    memoria.guardar_entrada(root, "Dos", "Segunda nota.", ["Vida"])
    r = indice.reindexar(root, geo=False)
    assert r["entradas"] == 2 and r["con_embedding"] == 2
    assert r["ms"] >= 0 and r["geocodificados"] == 0


def test_buscar_hibrido_mantiene_el_contrato_de_formato(root):
    """chat._retrieval_scripted extrae los (....md) de buscar() con regex; el
    camino híbrido tiene que producir exactamente el mismo formato de hit."""
    memoria.guardar_entrada(root, "Compra del coche", "Compramos un coche eléctrico usado.",
                            ["Vida"], adjunto="07_Inbox/_adjuntos/coche.jpg")
    out = memoria.buscar(root, "coche electrico")
    rutas = re.findall(r"\(([\w./ -]+?\.md)\)", out)   # el regex exacto de chat.py
    assert "Entradas/compra-del-coche.md" in rutas
    primera = out.splitlines()[0]
    assert primera.startswith("- [Compra del coche](Entradas/compra-del-coche.md)")
    assert "· medio: /attach/07_Inbox/_adjuntos/coche.jpg" in primera


def test_buscar_cae_al_naive_sin_indice(root, monkeypatch):
    memoria.guardar_entrada(root, "Nota sin indice", "Texto sobre telescopios.", ["Ciencia"])
    monkeypatch.setattr(indice, "buscar_hibrida", lambda *a, **k: [])
    out = memoria.buscar(root, "telescopios")
    assert "Entradas/nota-sin-indice.md" in out


def test_buscar_memorias_relevancia_y_capturado(root):
    memoria.guardar_entrada(root, "Sobre cohetes", "El cohete de agua del taller.", ["Ciencia"],
                            meta={"capturado": "2026-08-01T10:00:00-05:00"})
    memoria.guardar_entrada(root, "Sin relación", "Jardinería de interior.", ["Casa"])
    out = memoria.buscar_memorias(root, texto="cohete", orden="relevancia")
    assert out and out[0]["slug"] == "sobre-cohetes"
    assert out[0]["capturado"] == "2026-08-01T10:00:00-05:00"
    # los órdenes léxicos no reciben vecinos semánticos de regalo
    solo_lexico = memoria.buscar_memorias(root, texto="jardineria")
    assert [m["slug"] for m in solo_lexico] == ["sin-relacion"]


def test_grafo_nodos_y_aristas(root):
    memoria.guardar_entrada(root, "Alfa", "Notas sobre jardines verticales urbanos.", ["Casa/Jardin"])
    memoria.guardar_entrada(root, "Beta", "Más notas sobre jardines verticales urbanos.", ["Casa/Jardin"])
    memoria.guardar_entrada(root, "Gama", "Ver [[alfa]] para el contexto.", ["Trabajo"])
    g = indice.grafo(root)
    ids = {n["id"] for n in g["nodos"]}
    assert {"e:alfa", "e:beta", "e:gama", "s:Casa", "s:Casa/Jardin", "s:Trabajo"} <= ids
    tipos = {(a["a"], a["b"], a["tipo"]) for a in g["aristas"]}
    assert ("s:Casa", "s:Casa/Jardin", "subject") in tipos      # jerarquía de subjects
    assert ("e:alfa", "e:beta", "semantico") in tipos or ("e:beta", "e:alfa", "semantico") in tipos
    assert ("e:gama", "e:alfa", "wikilink") in tipos
    assert all("peso" in a for a in g["aristas"] if a["tipo"] == "semantico")


def test_grafo_de_una_sesion_es_el_vecindario(root):
    """El mindmap de sesión pide el subgrafo de las memorias que citó: entran
    ellas, sus vecinas semánticas y con quien se enlazan — nada más."""
    memoria.guardar_entrada(root, "Alfa", "Notas sobre jardines verticales urbanos.", ["Casa/Jardin"])
    memoria.guardar_entrada(root, "Beta", "Más notas sobre jardines verticales urbanos.", ["Casa/Jardin"])
    memoria.guardar_entrada(root, "Gama", "Ver [[alfa]] para el contexto.", ["Trabajo"])
    memoria.guardar_entrada(root, "Delta", "Receta de sopa de calabaza y jengibre.", ["Cocina"])

    g = indice.grafo(root, ["alfa"])
    ids = {n["id"] for n in g["nodos"]}
    assert "e:alfa" in ids
    assert "e:beta" in ids          # vecina semántica (mismo tema)
    assert "e:gama" in ids          # la enlaza con [[alfa]]
    assert "e:delta" not in ids     # sin relación: fuera
    assert "s:Casa/Jardin" in ids   # los subjects de las incluidas sí entran
    assert all(a["a"] in ids and a["b"] in ids for a in g["aristas"])

    # lista vacía = nada seleccionado, NO la base entera
    assert indice.grafo(root, []) == {"nodos": [], "aristas": []}
    # sin argumento sigue siendo todo
    assert {"e:alfa", "e:delta"} <= {n["id"] for n in indice.grafo(root)["nodos"]}


def test_grafo_ejes_por_tag_y_lugar(root):
    """Dos memorias que comparten tag o lugar quedan conectadas A TRAVÉS de un
    nodo-eje que dice por qué. Un valor con una sola memoria no conecta nada y
    no se dibuja."""
    memoria.guardar_entrada(root, "Alfa", "Jardines verticales.", ["Casa"], tags=["verde", "solo-alfa"],
                            meta={"lugar": "Lima"})
    memoria.guardar_entrada(root, "Beta", "Huerta en macetas.", ["Casa"], tags=["verde"],
                            meta={"lugar": "Lima"})
    memoria.guardar_entrada(root, "Gama", "Otra cosa.", ["Trabajo"], meta={"lugar": "Cusco"})
    g = indice.grafo(root)
    ids = {n["id"] for n in g["nodos"]}
    assert {"t:verde", "l:Lima"} <= ids
    assert "t:solo-alfa" not in ids   # un solo dueño: no conecta a nadie
    assert "l:Cusco" not in ids
    aristas = {(a["a"], a["b"], a["tipo"]) for a in g["aristas"]}
    assert ("e:alfa", "t:verde", "tag") in aristas
    assert ("e:beta", "l:Lima", "lugar") in aristas
    assert all(a["a"] in ids and a["b"] in ids for a in g["aristas"])   # sin aristas colgando

    # en el mapa de UNA sesión los ejes se cuentan sobre las memorias visibles:
    # el vecindario de gama no incluye a beta, así que Lima deja de conectar
    assert "l:Lima" not in {n["id"] for n in indice.grafo(root, ["gama"])["nodos"]}


def _base_con_tailscale(root, privada=False):
    memoria.guardar_entrada(root, "Tailscale y WireGuard",
                            "Red mesh con WireGuard, funnel y certificados https.", ["Tecnologia/Redes"],
                            privada=privada)
    for i in range(6):   # relleno: sin base, el IDF no distingue nada de nada
        memoria.guardar_entrada(root, f"Receta {i}", f"Sopa de calabaza y jengibre, variante {i}.", ["Cocina"])


def test_sugerencias_piden_ancla_lexica(root):
    """"Recordar mientras se escribe" con base chica: manda el BM25, que exige
    una palabra que DISCRIMINE. El OR del FTS matchea media base por palabras
    vacías; ninguna de esas aporta puntaje, así que no sugieren nada."""
    _base_con_tailscale(root)
    hits = indice.sugerencias(root, "estaba viendo lo del funnel de tailscale y su certificado")
    assert [h["slug"] for h in hits] == ["tailscale-y-wireguard"]
    assert hits[0]["titulo"] == "Tailscale y WireGuard" and hits[0]["privada"] is False

    assert indice.sugerencias(root, "y de lo que se de la que no en el") == []
    assert indice.sugerencias(root, "corto") == []          # por debajo del piso
    assert indice.sugerencias(root, "  ") == []


def test_sugerencias_la_z_veta_cuando_hay_base(root, monkeypatch):
    """La segunda señal: con MIN_BASE_Z memorias o más, un acierto léxico que no
    sobresale semánticamente (z < 2) se cae igual. El piso existe porque la z
    tiene techo (N−1)/√N: con 7 memorias nunca llegaría a 2 y la función quedaría
    muerta en una base recién nacida."""
    _base_con_tailscale(root)
    assert indice.sugerencias(root, "estaba viendo lo del funnel de tailscale y su certificado")
    monkeypatch.setattr(indice, "MIN_BASE_Z", 1)   # prender la z sobre esta misma base
    assert indice.sugerencias(root, "estaba viendo lo del funnel de tailscale y su certificado") == []


def test_sugerencias_marcan_lo_privado(root):
    """El candado del celular necesita saberlo: la sugerencia lo declara y la
    PWA la esconde. Sin archivo (entrada fantasma) se asume privada."""
    _base_con_tailscale(root, privada=True)
    hits = indice.sugerencias(root, "estaba viendo lo del funnel de tailscale y su certificado")
    assert [h["privada"] for h in hits] == [True]
    assert indice._meta_entrada(root, "no-existe")["privada"] is True
    # ...y desde otro proyecto ni se sugiere: lo privado no sale del suyo (v88)
    assert indice.sugerencias(root, "estaba viendo lo del funnel de tailscale y su certificado",
                              proyecto="Otro") == []


def test_enlazar_entradas_confirma_con_linea_fechada(root):
    memoria.guardar_entrada(root, "Nota madre", "Texto base sobre drones.", ["Tecnologia"])
    memoria.guardar_entrada(root, "Nota hija", "Detalle del dron cuatrimotor.", ["Tecnologia"])
    out = memoria.enlazar_entradas(root, "nota-madre", "nota-hija")
    assert "[[nota-hija]]" in out
    cuerpo = (root / memoria.ENTRADAS / "nota-madre.md").read_text(encoding="utf-8")
    assert "(enlace) — vinculada con [[nota-hija]]." in cuerpo   # fechada, append-only
    assert memoria.enlazar_entradas(root, "nota-madre", "nota-hija") == "(ya estaban enlazadas)"
    with pytest.raises(FileNotFoundError):
        memoria.enlazar_entradas(root, "nota-madre", "no-existe")
    # el hook indexó el wikilink: aparece en conexiones de los dos lados
    cx = memoria.conexiones(root, "nota-madre")
    assert [w["slug"] for w in cx["wikilinks"]] == ["nota-hija"]
    cx = memoria.conexiones(root, "nota-hija")
    assert [w["slug"] for w in cx["backlinks"]] == ["nota-madre"]


def test_conexiones_sugiere_relacionadas_sin_enlazar(root):
    memoria.guardar_entrada(root, "Huerta en casa", "Plantar tomates y albahaca en macetas.", ["Casa"])
    memoria.guardar_entrada(root, "Huerta comunitaria", "Plantar tomates y acelga en canteros.", ["Casa"])
    cx = memoria.conexiones(root, "huerta-en-casa")
    rel = [r["slug"] for r in cx["relacionadas"]]
    assert "huerta-comunitaria" in rel and all("score" in r for r in cx["relacionadas"])
    # sugerir NO escribe: el cuerpo sigue sin wikilinks
    assert "[[" not in (root / memoria.ENTRADAS / "huerta-en-casa.md").read_text(encoding="utf-8")
    # confirmada la sugerencia, deja de sugerirse (ya es conexión)
    memoria.enlazar_entradas(root, "huerta-en-casa", "huerta-comunitaria")
    cx = memoria.conexiones(root, "huerta-en-casa")
    assert "huerta-comunitaria" not in [r["slug"] for r in cx["relacionadas"]]


def test_lint_reporta_duplicados_y_wikilinks_rotos(root):
    from mem import lint
    texto = ("Notas del taller de escritura del jueves: consignas, referencias "
             "y la lista completa de correcciones pendientes para la próxima sesión.")
    memoria.guardar_entrada(root, "Taller de escritura", texto, ["Vida"])
    memoria.guardar_entrada(root, "Taller de escritura dos", texto, ["Vida"])
    memoria.guardar_entrada(root, "Con link roto", "Apunta a [[fantasma]] que no existe.", ["Vida"])
    problemas = "\n".join(lint.correr(root))
    assert "posible duplicado" in problemas
    assert "taller-de-escritura.md" in problemas and "taller-de-escritura-dos.md" in problemas
    assert "wikilink roto: Entradas/con-link-roto.md → [[fantasma]]" in problemas


def test_sincronizar_debounce(root, monkeypatch):
    """Dentro de la ventana SYNC_CADA no se barre; forzar (el hook de escritura,
    reindexar) sí. Los tests corren con MEM_SYNC_CADA=0 salvo este."""
    memoria.guardar_entrada(root, "Nota base", "Texto sobre faros.", ["Vida"])   # hook: stamp fijado
    monkeypatch.setattr(indice, "SYNC_CADA", 3600.0)
    (root / memoria.ENTRADAS / "externa.md").write_text(
        "# Externa\n\nzanahoria silvestre\n", encoding="utf-8")
    hits = indice.buscar_hibrida(root, "zanahoria")
    assert all(s != "externa" for s, _ in hits)     # debounced: la edición externa no se ve aún
    assert indice.sincronizar(root, forzar=True) >= 1
    hits = indice.buscar_hibrida(root, "zanahoria")
    assert hits and hits[0][0] == "externa"


def test_cache_matriz_se_invalida_al_escribir(root):
    memoria.guardar_entrada(root, "Bosque andino", "Caminata entre árboles de queñua.", ["Viajes"])
    memoria.guardar_entrada(root, "Bosque nublado", "Caminata entre árboles y neblina.", ["Viajes"])
    assert indice.vecinas(root, "bosque-andino", umbral=0.3)   # carga y cachea la matriz
    assert str(root) in indice._MATRICES
    memoria.guardar_entrada(root, "Bosque seco", "Caminata entre árboles y cactus.", ["Viajes"])
    vs = [s for s, _ in indice.vecinas(root, "bosque-andino", umbral=0.3)]
    assert "bosque-seco" in vs   # el hook invalidó el cache: la matriz nueva la incluye


def test_estado_de_busqueda(root, monkeypatch):
    assert indice.estado(root)["hibrida"] is False   # vault virgen: sin modelo todavía
    memoria.guardar_entrada(root, "Nota con vector", "Texto sobre veleros.", ["Vida"])
    assert indice.estado(root) == {"hibrida": True, "motivo": "ok"}
    assert "solo léxica" not in memoria.buscar(root, "veleros")   # híbrida: sin nota
    # cambió el modelo: degrada, y buscar() lo dice en una línea que el regex
    # de rutas del chat NO puede confundir con un hit
    con = indice.abrir(root)
    indice._meta_set(con, "modelo_embed", "otro-modelo")
    con.commit()
    con.close()
    e = indice.estado(root)
    assert e["hibrida"] is False and "modelo" in e["motivo"]
    ultima = memoria.buscar(root, "veleros").splitlines()[-1]
    assert "solo léxica" in ultima
    assert not re.findall(r"\(([\w./ -]+?\.md)\)", ultima)
    monkeypatch.setattr(indice, "abrir", lambda *a, **k: None)
    e = indice.estado(root)
    assert e["hibrida"] is False and "FTS5" in e["motivo"]


def test_conexiones_por_indice_cap_orden_y_dedupe(root):
    memoria.guardar_entrada(root, "Origen", "Nota origen.", ["Tecnologia/IA", "Ciencia"])
    memoria.guardar_entrada(root, "Ambos", "Comparte los dos temas.", ["Tecnologia/IA", "Ciencia"])
    for i in range(6):
        memoria.guardar_entrada(root, f"Relleno {i}", f"Nota de relleno {i}.", ["Ciencia"])
    cx = memoria.conexiones(root, "origen")
    slugs = [e["slug"] for e in cx["entradas"]]
    assert len(slugs) == 5 == len(set(slugs))   # cap 5 y sin duplicar la que comparte 2 subjects
    assert slugs == sorted(slugs)               # orden por slug
    assert "ambos" in slugs


def test_conexiones_sin_indice_cae_al_scan(root, monkeypatch):
    memoria.guardar_entrada(root, "Solo A", "Nota A.", ["Vida"])
    memoria.guardar_entrada(root, "Solo B", "Nota B.", ["Vida"])
    monkeypatch.setattr(indice, "abrir", lambda *a, **k: None)
    cx = memoria.conexiones(root, "solo-a")
    assert cx["entradas"] == [{"slug": "solo-b", "titulo": "Solo B"}]
    assert cx["wikilinks"] == [] and cx["backlinks"] == [] and cx["relacionadas"] == []


def test_modelo_cambiado_invalida_vectores(root, monkeypatch):
    memoria.guardar_entrada(root, "Nota estable", "Texto sobre bicicletas.", ["Vida"])
    con = indice.abrir(root)
    indice._meta_set(con, "modelo_embed", "otro-modelo")
    con.execute("UPDATE entradas SET vec=NULL")
    con.commit()
    con.close()
    # con vectores de otro modelo no se embebe ni se mezcla: BM25 sigue andando
    hits = indice.buscar_hibrida(root, "bicicletas")
    assert hits and hits[0][0] == "nota-estable"
    con = indice.abrir(root)
    assert con.execute("SELECT count(vec) FROM entradas").fetchone()[0] == 0
    con.close()
    # reindexar limpia meta y regenera los vectores con el modelo actual
    r = indice.reindexar(root, geo=False)
    assert r["con_embedding"] == 1
