"""Chequeos mínimos del motor sobre una base hamuQ de fixture (nunca la real)."""
import json
import os
import re
from pathlib import Path

import frontmatter
import pytest

from mem import agentes, chat, config, lint, media, memoria, modos, procesar, sesiones, triaje, youtube


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "hamuQ"
    for d in ("09_Sistema/Modos", "06_Biblioteca_Conocimiento/Entradas", "08_Categorias", "10_Sesiones", "07_Inbox"):
        (r / d).mkdir(parents=True)
    (r / "09_Sistema/NUCLEO.md").write_text("# NUCLEO\nDiego. hamuQ es la memoria.", encoding="utf-8")
    (r / "09_Sistema/log.md").write_text("# Log\n", encoding="utf-8")
    (r / "00_INDICE_GENERAL.md").write_text("# Índice\n- proyecto VR museo", encoding="utf-8")
    (r / "08_Categorias/00_CATEGORIAS.md").write_text(
        "# Categorías\n\n## Tecnologia\n- **IA** — ia\n- **Inmersivo** — vr\n", encoding="utf-8")
    (r / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md").write_text(
        "# Índice Temático\n\n## Por etiqueta/categoría\n\n### Inmersivo\n"
        "- [Gaussian Splatting](Entradas/gaussian-splatting.md) — captura volumétrica. (2026-07-30)\n\n"
        "## Todas las entradas (orden cronológico)\n", encoding="utf-8")
    (r / "06_Biblioteca_Conocimiento/Entradas/gaussian-splatting.md").write_text(
        "# Gaussian Splatting\n\nTécnica de captura volumétrica para VR en tiempo real.", encoding="utf-8")
    (r / "09_Sistema/Modos/chat.md").write_text(
        "---\nnombre: chat\nretrieval: agentic\n"
        "herramientas: [buscar, leer_pagina, grep]\nmax_paginas: 2\n---\nEres el asistente.",
        encoding="utf-8")
    return r


def test_pedir_medios_cambia_al_modo_media(root):
    """Pedir 'generá una imagen' en modo chat no puede terminar en 'no puedo': el
    turno lo atiende el modo media. Y una pregunta SOBRE un medio ya guardado no
    debe secuestrarse — eso es leer la memoria, no crear."""
    from mem import chat

    crear_medios = ["Quiero que generes la imagen de un caballo volador",
                    "genera un video de una alpaca caminando", "dibuja un logo",
                    "genérame una foto del atardecer", "generate an image of a horse"]
    memoria_no_taller = ["busca la imagen que guardé ayer", "hazme un resumen de la imagen que subí",
                         "qué animales vi en el viaje", "crea un resumen de la reunión",
                         "transcribe el audio que subí", "mostrame el dibujo que guardé"]
    assert all(chat.pide_medios(t) for t in crear_medios)
    assert not any(chat.pide_medios(t) for t in memoria_no_taller)

    chat_modo = modos.cargar(root, "chat")
    # sin modo media instalado se responde con el que había, no se rompe el turno
    assert chat._modo_para(root, chat_modo, crear_medios[0]) == (chat_modo, False)

    (root / "09_Sistema/Modos/media.md").write_text(
        "---\nnombre: media\nagente: crear\nherramientas: [buscar, crear]\n---\nTaller.", encoding="utf-8")
    modo, cambio = chat._modo_para(root, chat_modo, crear_medios[0])
    assert cambio and modo["nombre"] == "media" and "crear" in modo["herramientas"]
    # una pregunta normal se queda en chat, y estando YA en media no se toca nada
    assert chat._modo_para(root, chat_modo, "qué proyectos tengo") == (chat_modo, False)
    media = modos.cargar(root, "media")
    assert chat._modo_para(root, media, crear_medios[0]) == (media, False)

    # Los ajustes al medio recién generado también son del taller (2026-08-07): los
    # tres que Diego escribió después del video se quedaron en modo chat, con un
    # agente sin herramientas, y terminaron en un "necesito tu permiso" sin salida.
    ajustes = ["puedes hacer el mismo video pero en formato 16:9 y que dure más tiempo?",
               "re intentalo en higgsfield", "si, hazlo en comfyUI local"]
    for t in ajustes:
        assert chat._modo_para(root, chat_modo, t, True)[1], t      # con el video a la vista
        assert not chat._modo_para(root, chat_modo, t, False)[1], t  # suelto no significa nada

    # si alguien le saca las herramientas a media, cambiar de modo no arreglaría nada
    (root / "09_Sistema/Modos/media.md").write_text(
        "---\nnombre: media\nherramientas: [buscar]\n---\nTaller.", encoding="utf-8")
    assert chat._modo_para(root, chat_modo, crear_medios[0]) == (chat_modo, False)


def test_buscar_encuentra_por_indices(root):
    out = memoria.buscar(root, "captura volumétrica gaussian")
    assert "gaussian-splatting.md" in out


def test_leer_pagina_resuelve_relativo_y_es_segura(root):
    assert "volumétrica" in memoria.leer_pagina(root, "Entradas/gaussian-splatting.md")
    assert memoria.leer_pagina(root, "../fuera.md") == "(path fuera de la base)"
    assert memoria.leer_pagina(root, "no/existe.md").startswith("(no existe")


def test_leer_pagina_paginada(root):
    """Las páginas largas ya no se cortan en silencio: vienen en partes con aviso."""
    (root / memoria.ENTRADAS / "larga.md").write_text(
        "x" * (memoria.MAX_PAGINA_CHARS + 5), encoding="utf-8")
    p1 = memoria.leer_pagina(root, "Entradas/larga.md")
    assert p1.startswith("xxxx") and "parte 1/2" in p1 and "parte=2" in p1
    p2 = memoria.leer_pagina(root, "Entradas/larga.md", parte=2)
    assert p2.startswith("xxxxx\n") and "parte 2/2" in p2 and "fin" in p2
    assert memoria.leer_pagina(root, "Entradas/larga.md", parte=3).startswith("(parte 3 fuera de rango")
    # una página corta sale entera, sin sufijo
    assert "parte" not in memoria.leer_pagina(root, "Entradas/gaussian-splatting.md")


def test_grep_fallback(root):
    out = memoria.grep(root, "tiempo real")
    assert "gaussian-splatting.md" in out


def test_guardar_entrada_crea_indexa_y_dedupe(root):
    r1 = memoria.guardar_entrada(root, "Prueba LLMs", "Los LLMs sirven para X.", ["IA"], tags=["llm"])
    assert "creada" in r1
    entrada = root / "06_Biblioteca_Conocimiento/Entradas/prueba-llms.md"
    assert entrada.exists()
    meta = frontmatter.load(entrada).metadata
    assert meta["subjects"] == ["Proyectos/General", "IA"] and meta["tags"] == ["llm"]  # legible por cualquier agente
    iid, v1 = meta["id"], meta["version"]
    assert v1 == 1
    tematico = (root / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md").read_text(encoding="utf-8")
    assert "prueba-llms.md" in tematico and "### IA" in tematico
    categorias = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    assert "prueba-llms.md" in categorias
    # mismo título de nuevo → actualiza, no duplica
    r2 = memoria.guardar_entrada(root, "Prueba LLMs", "Dato nuevo.", ["IA"])
    assert "actualizada" in r2
    assert "Dato nuevo." in entrada.read_text(encoding="utf-8")
    # ...y la anterior quedó guardada entera, con el mismo id
    meta2 = frontmatter.load(entrada).metadata
    assert meta2["id"] == iid and meta2["version"] == 2
    vieja = root / f"06_Biblioteca_Conocimiento/Entradas/_versiones/{iid}/v001.md"
    assert vieja.exists() and "Dato nuevo." not in vieja.read_text(encoding="utf-8")


def test_editar_resumen_versiona_y_conserva_registro(root):
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"])
    r = memoria.editar_entrada(root, "nota-ia", resumen="Sobre agentes y herramientas.")
    assert "editada" in r
    texto = (root / "06_Biblioteca_Conocimiento/Entradas/nota-ia.md").read_text(encoding="utf-8")
    assert "Sobre agentes y herramientas." in texto
    assert "entrada creada." in texto              # el registro histórico nunca se pisa
    assert memoria.versiones(root, "nota-ia")[0]["version"] == 1
    assert "Sobre agentes." in memoria.version_leer(root, "nota-ia", 1)["contenido"]


def test_editar_solo_tags_no_versiona(root):
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"])
    memoria.editar_entrada(root, "nota-ia", tags=["llm"])
    assert memoria.versiones(root, "nota-ia") == []   # un toque de chip no es una versión


def test_entrada_vieja_gana_id_al_editarse(root):
    # gaussian-splatting.md viene del fixture sin frontmatter: el caso legacy
    memoria.editar_entrada(root, "gaussian-splatting", nota="revisada")
    meta = frontmatter.load(root / "06_Biblioteca_Conocimiento/Entradas/gaussian-splatting.md").metadata
    assert meta["id"] and meta["version"] == 2
    assert memoria.versiones(root, "gaussian-splatting")[0]["version"] == 1
    assert memoria.asignar_ids(root) == 0   # ya no queda ninguna sin id


def test_guardar_entrada_categoria_nueva_se_reporta(root):
    memoria.guardar_entrada(root, "Tema raro", "Contenido.", ["CategoriaInexistente"])
    assert "CategoriaInexistente" in (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    assert "CategoriaInexistente" in (root / "09_Sistema/log.md").read_text(encoding="utf-8")


def test_leer_log_filtra_por_horas(root):
    from datetime import datetime, timedelta
    memoria.log_evento(root, "test", "evento reciente")
    ahora = datetime.now()
    assert any("evento reciente" in l for l in memoria.leer_log(root, ahora - timedelta(hours=1)))
    assert not any("evento reciente" in l for l in memoria.leer_log(root, ahora + timedelta(hours=1)))


def test_log_evento_archiva_el_dia_anterior(root):
    import os
    from datetime import datetime, timedelta
    log_md = root / "09_Sistema/log.md"
    memoria.log_evento(root, "test", "evento de ayer")
    ayer = datetime.now() - timedelta(days=1)
    os.utime(log_md, (ayer.timestamp(), ayer.timestamp()))  # simula log.md sin tocar desde ayer

    memoria.log_evento(root, "test", "evento de hoy")

    archivo = root / "09_Sistema/Log_Archivo" / f"{ayer.date().isoformat()}.md"
    assert archivo.exists() and "evento de ayer" in archivo.read_text(encoding="utf-8")
    assert "evento de hoy" in log_md.read_text(encoding="utf-8")
    assert "evento de ayer" not in log_md.read_text(encoding="utf-8")


def test_buscar_memorias_filtros(root):
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"], tags=["agentes"])
    memoria.guardar_entrada(root, "Nota VR", "Sobre museos.", ["Inmersivo"], tags=["museo"])
    assert [m["slug"] for m in memoria.buscar_memorias(root, subject="IA")] == ["nota-ia"]
    assert [m["slug"] for m in memoria.buscar_memorias(root, tag="museo")] == ["nota-vr"]
    assert [m["slug"] for m in memoria.buscar_memorias(root, texto="agentes")] == ["nota-ia"]
    assert memoria.buscar_memorias(root, desde="2999-01-01") == []


def test_omnibox_filtra_al_escribir(root):
    """El buscador de Memory (orden=relevancia) es un FILTRO: lo que no dice lo
    escrito no sale. Y con 1-2 letras se mira por principio de palabra en los
    campos cortos — buscar "n" en el cuerpo devolvía la Biblioteca entera y la
    pantalla parecía no reaccionar al escribir (reportado 2026-09-06)."""
    memoria.guardar_entrada(root, "Rooftops de Bruselas", "Bares con vista.", ["Ocio"], tags=["rooftop"])
    memoria.guardar_entrada(root, "Museo del barroco", "Sin relación.", ["Arte"], tags=["museo"])
    omni = lambda q: {m["slug"] for m in memoria.buscar_memorias(root, texto=q, orden="relevancia")}
    assert omni("rooftop") == {"rooftops-de-bruselas"}
    assert omni("vista") == {"rooftops-de-bruselas"}          # el cuerpo cuenta con 3+ letras
    assert omni("ro") == {"rooftops-de-bruselas"}             # principio de palabra, no "barroco"
    assert omni("zzz") == set()


def test_editar_entrada_reindexa_y_registra(root):
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"])
    r = memoria.editar_entrada(root, "nota-ia", subjects=["Inmersivo"], nota="reclasificada")
    assert "editada" in r
    categorias = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    # el link quedó bajo Inmersivo y ya no bajo IA
    idx_ia = categorias.index("**IA**"); idx_inm = categorias.index("**Inmersivo**")
    assert "nota-ia.md" in categorias[idx_inm:] and "nota-ia.md" not in categorias[idx_ia:idx_inm]
    texto = (root / "06_Biblioteca_Conocimiento/Entradas/nota-ia.md").read_text(encoding="utf-8")
    assert "(edición)" in texto and "reclasificada" in texto  # cambio fechado, historia intacta
    assert memoria.editar_entrada(root, "nota-ia") == "(sin cambios)"


def test_arbol_subjects_con_conteos(root):
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"])
    arbol = memoria.arbol_subjects(root)
    tec = next(n for n in arbol if n["nombre"] == "Tecnologia")
    ia = next(n for n in tec["hijos"] if n["nombre"] == "IA")
    assert ia["entradas"] == 1


def test_capturar_inbox_fija_campos_del_usuario(root):
    rel = memoria.capturar(root, "idea: probar gaussian splatting en el museo",
                           contexto="VR museo", tags=["idea"], subjects=["Inmersivo"])
    p = root / rel
    assert p.exists()
    meta = frontmatter.load(p).metadata
    assert meta["estado"] == "pendiente" and meta["contexto_usuario"] == "VR museo"
    assert set(meta["fijado"]) == {"tags", "subjects"}  # el usuario manda sobre la máquina


def test_dos_capturas_del_mismo_segundo_no_se_pisan(root):
    """El id es fecha+segundo+slug: dos medios catalogados de corrido con texto
    parecido caían en el mismo archivo y la segunda captura se perdía."""
    texto = "imagen generada con ComfyUI el 2026-08-05: un caballo alado en el cielo"
    a = memoria.capturar(root, texto, adjunto="07_Inbox/_adjuntos/a.png")
    b = memoria.capturar(root, texto, adjunto="07_Inbox/_adjuntos/b.png")
    assert a != b and (root / a).exists() and (root / b).exists()
    assert {frontmatter.load(root / x).metadata["adjunto"] for x in (a, b)} == {
        "07_Inbox/_adjuntos/a.png", "07_Inbox/_adjuntos/b.png"}
    assert {frontmatter.load(root / x).metadata["id"] for x in (a, b)} == {
        (root / a).stem, (root / b).stem}  # el id del frontmatter sigue al nombre real


def test_inbox_editar_y_eliminar(root):
    rel = memoria.capturar(root, "capturar algo")
    iid = (root / rel).stem
    item = memoria.inbox_editar(root, iid, texto="texto corregido", tags=["x"])
    assert item["texto"] == "texto corregido" and set(item["fijado"]) == {"texto", "tags"}
    assert any(i["id"] == iid for i in memoria.inbox_listar(root))
    destino = memoria.inbox_eliminar(root, iid)
    assert "_papelera" in destino and (root / destino).exists()  # nada se borra
    assert not any(i["id"] == iid for i in memoria.inbox_listar(root))


def test_medio_subido_a_una_sesion_cuenta_como_referido(root):
    """Lo que Diego sube dentro de una sesión vive como ![x](/attach/…) en el
    diálogo y nunca tiene frontmatter `adjunto` propio (una sesión trae varios,
    el campo es uno). Sin mirar el cuerpo, cada procesado de esa sesión dejaba
    un "medio sin entrada" en el lint."""
    (root / "07_Inbox/_adjuntos").mkdir(parents=True)
    (root / "07_Inbox/_adjuntos/video.mp4").write_bytes(b"x")
    sid = "2026-08-10_100000_medio"
    memoria.sincronizar_sesion_inbox(
        root, sid, "[Diego]\nmirá esto\n\n![video.mp4](/attach/07_Inbox/_adjuntos/video.mp4)")
    assert memoria.adjuntos_referidos(root)["07_Inbox/_adjuntos/video.mp4"] == [f"07_Inbox/sesion-{sid}.md"]
    assert not [p for p in lint.correr(root) if "medio sin entrada" in p]
    # ...y una mención colgada (medio ya borrado, sesión vieja que lo enseñaba)
    # no inventa el aviso del otro lado del vínculo
    (root / "10_Sesiones/vieja.md").write_text(
        "![ido](/attach/07_Inbox/_adjuntos/ido.png)", encoding="utf-8")
    assert not [p for p in lint.correr(root) if "adjunto inexistente" in p]


def test_medios_todos_incluye_sueltos_y_catalogados(root):
    """La galería tiene que ver lo catalogado (su propia ficha) Y lo recién
    subido a una sesión que todavía no se destiló — buscar_memorias() por sí
    sola solo mira Entradas/, así que el Media Manager nunca mostraba el
    segundo grupo (reportado 2026-08-10: un video vivía en la sesión y no
    aparecía ni en la galería ni en Memory)."""
    (root / "07_Inbox/_adjuntos").mkdir(parents=True)
    (root / "07_Inbox/_adjuntos/video.mp4").write_bytes(b"x")
    sid = "2026-08-10_100000_medio"
    memoria.sincronizar_sesion_inbox(
        root, sid, "[Diego]\nmirá esto\n\n![video.mp4](/attach/07_Inbox/_adjuntos/video.mp4)")
    (root / "07_Inbox/_adjuntos/foto.png").write_bytes(b"x")
    (root / "06_Biblioteca_Conocimiento/Entradas/cohete.md").write_text(
        "---\ntitulo: Cohete\nadjunto: 07_Inbox/_adjuntos/foto.png\n---\nUn cohete.", encoding="utf-8")

    todo = {m["ruta"]: m for m in memoria.medios_todos(root)}
    assert todo["07_Inbox/_adjuntos/video.mp4"]["slug"] == ""
    assert todo["07_Inbox/_adjuntos/video.mp4"]["sesion"] == sid
    assert todo["07_Inbox/_adjuntos/foto.png"]["slug"] == "cohete"

    solo_la_sesion = memoria.medios_todos(root, sesion=sid)
    assert [m["ruta"] for m in solo_la_sesion] == ["07_Inbox/_adjuntos/video.mp4"]


def test_duplicado_necesita_solape_de_palabras(root):
    """El coseno solo propone candidatos: dos memorias del mismo tema (una idea
    que CITA a TRELLIS.2 y la ficha de TRELLIS.2) no son un duplicado."""
    assert lint.solape("el mismo texto sobre la memoria", "el mismo texto sobre la memoria") == 1.0
    assert lint.solape("Idea de app de prueba para generar objetos 3D del control remoto con TRELLIS.2",
                       "TRELLIS.2 de Microsoft genera mallas 3D de alta fidelidad desde una imagen") < lint.MIN_SOLAPE


def test_sincronizar_sesion_inbox_crea_y_actualiza_en_el_mismo_archivo(root):
    sid = "2026-08-09_120000_prueba"
    r1 = memoria.sincronizar_sesion_inbox(root, sid, "[Diego]\nhola")
    assert r1 == f"07_Inbox/sesion-{sid}.md"
    item = memoria.inbox_listar(root)[0]
    assert item["id"] == f"sesion-{sid}" and item["tipo"] == "sesion" and item["estado"] == "pendiente"
    assert item["texto"] == "[Diego]\nhola"

    r2 = memoria.sincronizar_sesion_inbox(root, sid, "[Diego]\nhola\n\n[Asistente]\nchau")
    assert r2 == r1
    assert len(memoria.inbox_listar(root)) == 1   # sigue siendo un solo archivo, no uno nuevo por mensaje
    item = memoria.inbox_listar(root)[0]
    assert item["texto"] == "[Diego]\nhola\n\n[Asistente]\nchau"


def test_sincronizar_sesion_inbox_reabre_desde_procesado(root):
    """"Se modificó algo en la sesión" también actualiza su procesamiento: si el
    item ya fue procesado, sincronizar vuelve a abrirlo — y el siguiente pase de
    procesar_item cae en la MISMA entrada (vía `entrada`, que no se toca acá)."""
    sid = "2026-08-09_130000_reproceso"
    procesado_dir = root / "07_Inbox/_procesado"
    procesado_dir.mkdir(parents=True)
    post = frontmatter.Post(
        "[Diego]\nviejo", id=f"sesion-{sid}", sesion=sid, proyecto="", capturado="2026-08-09T13:00:00",
        tipo="sesion", origen="chat", adjunto="", contexto_usuario="", tags=[], subjects=["IA"],
        fijado=[], estado="procesada", entrada=f"{memoria.ENTRADAS}/vieja-sintesis.md")
    (procesado_dir / f"sesion-{sid}.md").write_text(frontmatter.dumps(post), encoding="utf-8")

    memoria.sincronizar_sesion_inbox(root, sid, "[Diego]\nviejo\n\n[Diego]\nnuevo mensaje")

    assert not (procesado_dir / f"sesion-{sid}.md").exists()
    item = memoria.inbox_listar(root)[0]
    assert item["id"] == f"sesion-{sid}" and item["estado"] == "pendiente"
    assert item["entrada"] == f"{memoria.ENTRADAS}/vieja-sintesis.md"
    assert "nuevo mensaje" in item["texto"]


def test_sincronizar_sesion_inbox_respeta_privacidad_y_fijado(root):
    memoria.proyecto_guardar(root, "Terapia", True)
    sid = "2026-08-09_140000_privada"
    memoria.sincronizar_sesion_inbox(root, sid, "[Diego]\nhola", proyecto="Terapia")
    item = memoria.inbox_listar(root)[0]
    privs = memoria.privados(root)
    assert memoria.accesible(item, "Terapia", privs)
    assert not memoria.accesible(item, "", privs)
    assert item["subjects"] == ["Proyectos/Terapia"]

    memoria.inbox_editar(root, f"sesion-{sid}", subjects=["Proyectos/Terapia", "Salud"])  # Diego lo fija a mano
    memoria.sincronizar_sesion_inbox(root, sid, "[Diego]\nhola\n\n[Diego]\nmás", proyecto="Terapia")
    item = memoria.inbox_listar(root)[0]
    assert item["subjects"] == ["Proyectos/Terapia", "Salud"]   # lo fijado no se pisa


def test_destilado_hereda_privacidad_del_proyecto(root):
    """El destilado de una sesión hereda la privacidad de SU proyecto (pedido
    2026-09-05): sin campo propio que sembrar, así que un proyecto público
    destila público y uno privado, privado — accesible() lee el proyecto."""
    memoria.proyecto_guardar(root, "Casa nueva", False)   # público
    memoria.sincronizar_sesion_inbox(root, "2026-08-31_100000_obra",
                                     "[Diego]\nel presupuesto subió", proyecto="Casa nueva")
    pub = next(i for i in memoria.inbox_listar(root) if i["id"] == "sesion-2026-08-31_100000_obra")

    memoria.proyecto_guardar(root, "Terapia", True)   # privado
    memoria.sincronizar_sesion_inbox(root, "2026-08-31_100100_terapia",
                                     "[Diego]\nsesión de hoy", proyecto="Terapia")
    priv = next(i for i in memoria.inbox_listar(root) if i["id"] == "sesion-2026-08-31_100100_terapia")

    privs = memoria.privados(root)
    assert memoria.accesible(pub, "", privs)             # público: se ve desde Todo
    assert memoria.accesible(priv, "Terapia", privs)     # privado, desde el suyo, sí
    assert not memoria.accesible(priv, "Casa nueva", privs)   # desde otro proyecto, no
    assert not memoria.accesible(priv, "", privs)        # "Todo" es solo lo público
    assert not memoria.accesible(priv, None, privs)      # MCP/CLI sin proyecto: solo lo público
    # ...salvo la app con el candado abierto (X-Privado: 1 → CANDADO_ABIERTO):
    # ahí se ve todo lo privado, esté uno parado donde esté (pedido 2026-09-06)
    assert memoria.accesible(priv, memoria.CANDADO_ABIERTO, privs)
    assert not memoria.entradas_ocultas(root, memoria.CANDADO_ABIERTO)
    assert not memoria.paths_ocultos(root, memoria.CANDADO_ABIERTO)

    # una sesión SIN proyecto no se acota: no hay proyecto al que pertenecer
    memoria.sincronizar_sesion_inbox(root, "2026-08-31_100500_suelta", "[Diego]\nhola")
    suelta = next(i for i in memoria.inbox_listar(root) if i["id"] == "sesion-2026-08-31_100500_suelta")
    assert memoria.accesible(suelta, "", privs)
    assert memoria.accesible(suelta, None, privs)


def test_sesiones_ciclo_completo(root):
    sid = sesiones.crear(root, ["IA", "Inmersivo"], "prueba de sesión")
    sesiones.agregar_mensaje(root, sid, "Diego", "hola")
    sesiones.agregar_mensaje(root, sid, "Asistente", "hola, ¿qué necesitás?")
    meta, msgs = sesiones.cargar(root, sid)
    assert meta["modo"] == "chat" and meta["subjects"] == ["IA", "Inmersivo"] and len(msgs) == 2
    assert msgs[0]["rol"] == "Diego" and msgs[1]["texto"].startswith("hola,")
    sesiones.actualizar_meta(root, sid, modo="qa")  # cambiar vista no pierde nada
    meta, msgs = sesiones.cargar(root, sid)
    assert meta["modo"] == "qa" and len(msgs) == 2
    destino = sesiones.archivar(root, sid)
    assert "_archivo" in destino
    assert sesiones.cargar(root, sid)[1] == msgs   # archivada = fuera de la lista, no inaccesible
    assert not any(m["id"] == sid for m in sesiones.listar(root))

    papelera = sesiones.eliminar(root, sid)
    assert "_papelera" in papelera and (root / papelera).exists()   # nada se borra
    with pytest.raises(FileNotFoundError):
        sesiones.cargar(root, sid)


def test_sesion_sin_titulo_se_llama_por_su_fecha(root):
    """El chat crea la sesión sin nombre: la nombra su fecha y hora hasta grabarla."""
    sid = sesiones.crear(root, [], "")
    meta = sesiones.cargar(root, sid)[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", meta["titulo"]), meta["titulo"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}", sid), sid   # sin la fecha repetida en el nombre


def test_modos_usados_se_acumulan_y_crear_se_lee_como_media(root):
    sid = sesiones.crear(root, [], "vistas", modo="chat")
    sesiones.actualizar_meta(root, sid, modo="mindmap")
    sesiones.actualizar_meta(root, sid, modo="chat")   # volver no duplica
    assert sesiones.cargar(root, sid)[0]["modos_usados"] == ["chat", "mindmap"]
    # sesión vieja: sin modos_usados y con el nombre viejo del modo
    p = root / f"10_Sesiones/{sid}.md"
    p.write_text(p.read_text(encoding="utf-8").replace("modo: chat", "modo: crear")
                 .replace("modos_usados:\n- chat\n- mindmap\n", ""), encoding="utf-8")
    meta = sesiones.cargar(root, sid)[0]
    assert meta["modo"] == "media" and "modos_usados" not in meta
    sesiones.actualizar_meta(root, sid, modo="chat")   # el primer cambio siembra el historial
    assert sesiones.cargar(root, sid)[0]["modos_usados"] == ["media", "chat"]


def test_subjects_se_descubren_solos(root):
    memoria.guardar_entrada(root, "Prueba LLMs", "sobre LLMs.", ["Tecnologia/IA"])
    # los declara la página leída…
    assert memoria.subjects_de(root, ["06_Biblioteca_Conocimiento/Entradas/prueba-llms.md"]) == ["Proyectos/General", "Tecnologia/IA"]
    assert memoria.subjects_de(root, ["no/existe.md"]) == []
    # …y si no se leyó nada de la base, los que la pregunta nombra
    assert memoria.subjects_mencionados(root, "¿qué hay de inmersivo?") == ["Tecnologia/Inmersivo"]
    assert memoria.subjects_mencionados(root, "nada que ver") == []
    assert memoria.subjects_mencionados(root, "IA") == []   # hojas cortas no matchean: mucho ruido


def test_modos_carga_con_defaults(root):
    m = modos.cargar(root, "chat")
    assert m["max_paginas"] == 2 and m["retrieval"] == "agentic" and "asistente" in m["prompt"]


class FakeLLM:
    """Guion fijo: buscar → leer_pagina → respuesta final."""
    def __init__(self):
        self.paso = 0

    def completar(self, system, msgs, tools=None):
        self.paso += 1
        if self.paso == 1:
            return {"texto": "", "tool_calls": [{"id": "t1", "name": "buscar", "args": {"consulta": "gaussian"}}],
                    "tokens_in": 100, "tokens_out": 10}
        if self.paso == 2:
            return {"texto": "", "tool_calls": [{"id": "t2", "name": "leer_pagina",
                    "args": {"path": "06_Biblioteca_Conocimiento/Entradas/gaussian-splatting.md"}}],
                    "tokens_in": 200, "tokens_out": 10}
        return {"texto": "Según gaussian-splatting.md, es captura volumétrica.", "tool_calls": [],
                "tokens_in": 300, "tokens_out": 50}


def test_turno_completo_con_persistencia(root, monkeypatch):
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeLLM())
    cfg = {"hamuq": root}
    sid = sesiones.crear(root, ["Inmersivo"])
    texto, paginas, tokens = chat.turno(cfg, sid, "¿qué hay de captura volumétrica?")
    assert "volumétrica" in texto
    assert paginas == ["06_Biblioteca_Conocimiento/Entradas/gaussian-splatting.md"]
    assert tokens == 600  # medido y acumulado
    meta, msgs = sesiones.cargar(root, sid)
    assert len(msgs) == 2 and meta["turnos"] == 1
    assert meta["tokens_entrada_ultimo_turno"] == 600
    # el turno mandó un solo mensaje (la pregunta): eso es todo lo destilable
    assert meta["tokens_historial_ultimo_turno"] == len("¿qué hay de captura volumétrica?") // 4
    assert meta["titulo"].startswith("¿qué hay")


class FakeLLMSinTools:
    """Proveedor sin tool-calling (p.ej. claude_code): el motor precarga contexto."""
    tools_ok = False

    def completar(self, system, msgs, tools=None):
        assert not tools  # nunca debe recibir tools
        assert "Contexto recuperado" in msgs[-1]["content"]
        return {"texto": "respuesta scripted", "tool_calls": [], "tokens_in": 50, "tokens_out": 5}


def test_proveedor_sin_tools_usa_scripted(root, monkeypatch):
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeLLMSinTools())
    modo = modos.cargar(root, "chat")
    texto, _, _ = chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "gaussian splatting"}])
    assert texto == "respuesta scripted"


class FakeLLMPidePermiso:
    """Guion: el agente Cheap (débil siguiendo instrucciones) pide autorización
    de más para crear_imagen pese a que media.md ya la da por sentada — visto en
    vivo 2026-08-06. No hay UI para aprobar esa pregunta suelta: se le contesta
    sola una vez y sigue."""
    def __init__(self):
        self.pasos = []
        self.forzados = []

    def completar(self, system, msgs, tools=None, forzar_tool=""):
        self.pasos.append(list(msgs))
        self.forzados.append(forzar_tool)
        if len(self.pasos) == 1:
            return {"texto": "Necesito que autorices el uso de crear_imagen para editar la referencia. ¿Lo autorizas?",
                    "tool_calls": [], "tokens_in": 10, "tokens_out": 5}
        return {"texto": "Listo, generé la imagen.", "tool_calls": [], "tokens_in": 20, "tokens_out": 5}


def test_pide_autorizacion_de_mas_para_crear_imagen_se_autocontesta(root, monkeypatch):
    fake = FakeLLMPidePermiso()
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: fake)
    modo = {"prompt": "modo de prueba", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    texto, _, tokens = chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "modifica la imagen"}])
    assert texto == "Listo, generé la imagen."
    assert len(fake.pasos) == 2 and tokens == 30
    # el segundo llamado ve la pregunta + el "sí" inyectado, no la pregunta sola
    ultimos = fake.pasos[1][-2:]
    assert ultimos[0]["role"] == "assistant" and "autoriz" in ultimos[0]["content"].lower()
    assert ultimos[1]["role"] == "user" and "autorizado" in ultimos[1]["content"].lower()
    # el segundo intento no confía solo en el texto: fuerza la tool por API
    assert fake.forzados == ["", "crear_imagen"]


def test_claude_code_preautoriza_lo_local_y_no_lo_que_cuesta():
    """El CLI corre headless: un tool que no esté en --allowedTools se deniega y no
    hay forma de aprobarlo. crear_imagen FALTABA, y por eso salía 'necesito tu
    permiso para lanzar la generación con ComfyUI (mcp__mem__crear_imagen)' por más
    que media.md dijera lo contrario (reportado 2026-08-06)."""
    from mem import llm as llm_mod, mcp
    assert "crear_imagen" in llm_mod._MCP_MEM_OK and "crear_video" in llm_mod._MCP_MEM_OK
    # lo que gasta plata no: por este camino el diálogo de la app no llega a correr
    assert "crear_nube" not in llm_mod._MCP_MEM_OK
    assert "procesar_inbox" not in llm_mod._MCP_MEM_OK
    # y todos existen de verdad en el server MCP (un typo acá se traga el permiso)
    assert set(llm_mod._MCP_MEM_OK) <= set(mcp.NOMBRES)


def test_forzar_tool_usa_required_en_openai_compat(monkeypatch):
    """LM Studio solo acepta tool_choice none/auto/required: con el objeto
    {"function": {...}} contesta 400 y el turno entero se cae (medido 2026-08-06).
    Anthropic sí quiere el objeto — cada adaptador manda lo suyo."""
    from mem import llm as llm_mod

    enviados = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            self.chat = self
            self.completions = self

        def create(self, **kw):
            enviados.update(kw)
            class R:
                choices = [type("C", (), {"message": type("M", (), {"content": "x", "tool_calls": []})()})()]
                usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()
            return R()

    monkeypatch.setattr(llm_mod, "_OpenAICompat", llm_mod._OpenAICompat)
    cli = llm_mod._OpenAICompat.__new__(llm_mod._OpenAICompat)
    cli.client, cli.modelo = FakeOpenAI(), "qwen"
    tools = [{"name": "crear_imagen", "description": "d", "parameters": {"type": "object", "properties": {}}}]

    cli.completar("s", [{"role": "user", "content": "hola"}], tools, forzar_tool="crear_imagen")
    assert enviados["tool_choice"] == "required"       # string, NUNCA un dict

    enviados.clear()
    cli.completar("s", [{"role": "user", "content": "hola"}], tools)
    assert "tool_choice" not in enviados               # sin forzar no se manda nada


class FakeLLMSinToolsPidePermiso:
    """El agente Smart va por el CLI de Claude Code: tools_ok=False, así que el motor
    NO le pasa tools y crea por el MCP propio del CLI. La red de seguridad miraba la
    lista de tools —vacía justo para este proveedor— y nunca se disparaba."""
    tools_ok = False
    mcp_propio = True      # el CLI trae crear_imagen por su MCP aunque el modo no la declare

    def __init__(self):
        self.pasos = []

    def completar(self, system, msgs, tools=None, forzar_tool=""):
        self.pasos.append(list(msgs))
        assert not tools
        assert not forzar_tool     # sin tool-calling no hay nada que forzar
        if len(self.pasos) == 1:
            return {"texto": "Necesito tu permiso para lanzar la generación de imagen con "
                             "ComfyUI ( mcp__mem__crear_imagen ). ¿Apruebo la llamada?",
                    "tool_calls": [], "tokens_in": 10, "tokens_out": 5}
        return {"texto": "Listo, la modifiqué.", "tool_calls": [], "tokens_in": 10, "tokens_out": 5}


def test_pide_permiso_en_modo_chat_por_el_mcp_del_cli_se_autocontesta(root, monkeypatch):
    """El caso del reporte: modo CHAT (no declara `crear`) + agente claude_code. El
    CLI ve mcp__mem__crear_imagen por su MCP propio y pide permiso; la vigilancia se
    activa igual, si no la pregunta quedaba sin salida."""
    fake = FakeLLMSinToolsPidePermiso()
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: fake)
    modo = {"prompt": "asistente", "herramientas": ["buscar", "mcp"], "max_paginas": 5,
            "retrieval": "agentic"}
    texto, _, _ = chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "modificá el cohete"}])
    assert texto == "Listo, la modifiqué."
    assert len(fake.pasos) == 2
    assert "autorizado" in fake.pasos[1][-1]["content"].lower()


def test_pide_permiso_sin_nombrar_la_tool_igual_se_detecta():
    """No siempre nombra crear_imagen: '¿Apruebo lanzar la generación con ComfyUI?'
    es la misma pregunta sin salida."""
    assert chat._pide_autorizacion_de_mas("¿Apruebo lanzar la generación de imagen con ComfyUI?")
    assert chat._pide_autorizacion_de_mas("Necesito que apruebes el permiso para crear_imagen. ¿Lo autorizás?")
    # sin pregunta, o sin pedido de permiso, no se toca
    assert not chat._pide_autorizacion_de_mas("Genero la imagen con ComfyUI y te la muestro.")
    assert not chat._pide_autorizacion_de_mas("¿Querés que la imagen sea más oscura?")


def test_pide_autorizacion_no_se_autocontesta_si_menciona_video(root, monkeypatch):
    """crear_video sí debe esperar el ok real (tarda y ocupa la GPU, política de
    media.md): si la pregunta lo nombra, no se autocontesta."""
    class FakeLLMPreguntaVideo:
        def completar(self, system, msgs, tools=None):
            return {"texto": "¿Confirmas que autorizo crear_video o crear_imagen para esto?",
                    "tool_calls": [], "tokens_in": 10, "tokens_out": 5}
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeLLMPreguntaVideo())
    modo = {"prompt": "modo de prueba", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    texto, _, _ = chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "hazme un video"}])
    assert texto.startswith("¿Confirmas")


class FakeLLMResultadoFantasma:
    """Peor todavía que pedir permiso: narra un '✅ Imagen generada' con un
    ![...](/attach/...) a un archivo que jamás generó (no llamó a la tool en
    absoluto) — visto en vivo 2026-08-06, dos veces, con distintos prompts."""
    def __init__(self):
        self.pasos = []
        self.forzados = []

    def completar(self, system, msgs, tools=None, forzar_tool=""):
        self.pasos.append(list(msgs))
        self.forzados.append(forzar_tool)
        if len(self.pasos) == 1:
            return {"texto": "✅ Imagen generada: ![gato](/attach/07_Inbox/_adjuntos/no-existe.png)",
                    "tool_calls": [], "tokens_in": 10, "tokens_out": 5}
        return {"texto": "Ahora sí, imagen generada de verdad.", "tool_calls": [], "tokens_in": 15, "tokens_out": 5}


def test_resultado_fantasma_de_crear_imagen_se_corrige(root, monkeypatch):
    fake = FakeLLMResultadoFantasma()
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: fake)
    modo = {"prompt": "modo de prueba", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    texto, _, _ = chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "hazme un gato"}])
    assert texto == "Ahora sí, imagen generada de verdad."
    assert len(fake.pasos) == 2
    assert "no existe" in fake.pasos[1][-1]["content"].lower()
    assert fake.forzados == ["", "crear_imagen"]


class FakeLLMLlamaLocal:
    """El fallo real (2026-08-07): Ajustes decía nube y el modelo llamó igual a la
    tool local, porque la preferencia era solo una línea del system prompt."""
    def __init__(self):
        self.pasos = 0

    def completar(self, system, msgs, tools=None, forzar_tool=""):
        self.pasos += 1
        if self.pasos == 1:
            return {"texto": "", "tokens_in": 5, "tokens_out": 1,
                    "tool_calls": [{"id": "1", "name": "crear_imagen", "args": {"prompt": "a rocket"}}]}
        return {"texto": "listo", "tool_calls": [], "tokens_in": 5, "tokens_out": 1}


def test_ajustes_media_manda_sobre_la_tool_que_eligio_el_modelo(root, monkeypatch):
    """Con imagen=nube y un job_type por defecto, una llamada a crear_imagen se
    ejecuta como crear_nube — y por ser de nube pasa por el diálogo de Diego."""
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeLLMLlamaLocal())
    hecho = {}
    monkeypatch.setattr(chat.crear, "ejecutar",
                        lambda cfg, nombre, args, **kw: hecho.update(nombre=nombre, args=args) or "ok")
    cfg = {"hamuq": root, "media_imagen": "higgsfield", "higgs_imagen": "nano_banana_pro"}
    modo = {"prompt": "taller", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    vistos = []

    def confirmar(nombre, args):
        vistos.append(nombre)
        return args                      # Diego dice que sí, sin cambiar nada

    chat.responder(cfg, modo, [{"role": "user", "content": "hazme una imagen de un cohete"}],
                   on_confirm=confirmar)
    assert hecho["nombre"] == "crear_nube" and hecho["args"]["modelo"] == "nano_banana_pro"
    assert vistos == ["crear_nube"]      # lo local nunca pregunta; lo de nube siempre


def test_pedir_local_en_el_mensaje_gana_sobre_el_ajuste(root, monkeypatch):
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeLLMLlamaLocal())
    hecho = {}
    monkeypatch.setattr(chat.crear, "ejecutar",
                        lambda cfg, nombre, args, **kw: hecho.update(nombre=nombre) or "ok")
    cfg = {"hamuq": root, "media_imagen": "higgsfield", "higgs_imagen": "nano_banana_pro"}
    modo = {"prompt": "taller", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    chat.responder(cfg, modo, [{"role": "user", "content": "hazme una imagen de un cohete, en local"}])
    assert hecho["nombre"] == "crear_imagen"


def test_confirmar_devuelve_los_params_elegidos_en_el_dialogo(root, monkeypatch):
    """El diálogo no es solo sí/no: lo que Diego elige ahí (duración, resolución)
    es lo que se ejecuta (pedido 2026-08-07)."""
    class FakeNube:
        def __init__(self):
            self.pasos = 0

        def completar(self, system, msgs, tools=None, forzar_tool=""):
            self.pasos += 1
            if self.pasos == 1:
                return {"texto": "", "tokens_in": 5, "tokens_out": 1,
                        "tool_calls": [{"id": "1", "name": "crear_nube",
                                        "args": {"modelo": "veo3_1", "prompt": "x"}}]}
            return {"texto": "listo", "tool_calls": [], "tokens_in": 5, "tokens_out": 1}

    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeNube())
    monkeypatch.setattr(chat.crear, "enrutar", lambda cfg, n, a, p, f="": (n, a))   # sin CLI
    hecho = {}
    monkeypatch.setattr(chat.crear, "ejecutar",
                        lambda cfg, nombre, args, **kw: hecho.update(args=args) or "ok")
    modo = {"prompt": "taller", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "un video"}],
                   on_confirm=lambda n, a: {**a, "params": {"duration": "4"}})
    assert hecho["args"]["params"] == {"duration": "4"}

    # y un "no" sigue siendo un no: la generación no llega a ejecutarse
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeNube())
    hecho.clear()
    chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "un video"}],
                   on_confirm=lambda n, a: None)
    assert not hecho


def test_resultado_con_adjunto_real_no_se_corrige(root, monkeypatch):
    """Un ![...](/attach/...) a un archivo que SÍ está en la base (un resultado
    real, o una mención normal a algo de la memoria) no dispara la corrección."""
    real = root / "07_Inbox/_adjuntos/gato.png"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"x")

    class FakeLLMReal:
        def completar(self, system, msgs, tools=None):
            return {"texto": "✅ Imagen generada: ![gato](/attach/07_Inbox/_adjuntos/gato.png)",
                    "tool_calls": [], "tokens_in": 10, "tokens_out": 5}
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeLLMReal())
    modo = {"prompt": "modo de prueba", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    texto, _, tokens = chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "hazme un gato"}])
    assert "gato.png" in texto and tokens == 10   # una sola pasada: no se corrigió nada


def test_turno_con_adjunto_lo_lee_y_lo_deja_en_el_historial(root, monkeypatch):
    """Adjuntar en medio de una sesión: el modelo ve el contenido leído por el
    lector del inbox (y, si es imagen/video, la indicación de mandarlo como
    `referencia` a ComfyUI), pero en el historial persiste el MARKDOWN del medio
    — la app lo muestra embebido, igual que los generados. Lo que no se sabe
    leer se le dice al modelo, no se pierde en silencio."""
    visto = {}

    class FakeVe:
        def completar(self, system, msgs, tools=None):
            visto["ultimo"] = msgs[-1]["content"]
            return {"texto": "ok", "tool_calls": [], "tokens_in": 10, "tokens_out": 1}

    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeVe())
    (root / "07_Inbox/_adjuntos").mkdir(parents=True, exist_ok=True)
    (root / "07_Inbox/_adjuntos/notas.txt").write_text("presupuesto: 3 vigas", encoding="utf-8")
    cfg = {"hamuq": root}
    sid = sesiones.crear(root, ["Inmersivo"])

    chat.turno(cfg, sid, "¿qué dice esto?", adjuntos=["07_Inbox/_adjuntos/notas.txt"])
    _, msgs = sesiones.cargar(root, sid)
    assert "![notas.txt](/attach/07_Inbox/_adjuntos/notas.txt)" in msgs[0]["texto"]
    assert "presupuesto" not in msgs[0]["texto"]       # la descripción no ensucia la burbuja
    assert "presupuesto: 3 vigas" in visto["ultimo"]   # pero el modelo sí la vio

    chat.turno(cfg, sid, "¿y esto?", adjuntos=["07_Inbox/_adjuntos/obra.mp4"])
    _, msgs = sesiones.cargar(root, sid)
    assert "![obra.mp4](/attach/07_Inbox/_adjuntos/obra.mp4)" in msgs[2]["texto"]
    # un video es material para ComfyUI: el modelo recibe la nota de `referencia`
    assert "no se pudo leer" in visto["ultimo"] and "referencia" in visto["ultimo"]

    # varios adjuntos en el mismo mensaje (pedido 2026-08-07): TODOS quedan en la
    # burbuja y TODOS se leen para el modelo — no solo el primero
    (root / "07_Inbox/_adjuntos/otra.txt").write_text("y 2 tirantes", encoding="utf-8")
    chat.turno(cfg, sid, "¿y estos dos?",
               adjuntos=["07_Inbox/_adjuntos/notas.txt", "07_Inbox/_adjuntos/otra.txt"])
    _, msgs = sesiones.cargar(root, sid)
    assert msgs[4]["texto"].count("](/attach/") == 2
    assert "presupuesto: 3 vigas" in visto["ultimo"] and "y 2 tirantes" in visto["ultimo"]


def test_turno_no_recarga_lo_destilado_y_mide_contexto(root, monkeypatch):
    """Pasar a memoria ACHICA los turnos: lo anterior al corte no vuelve a entrar
    al contexto aunque esté entre los últimos N mensajes."""
    visto = {}

    class FakeEco:
        def completar(self, system, msgs, tools=None):
            visto["msgs"] = msgs
            return {"texto": "ok", "tool_calls": [], "tokens_in": 10, "tokens_out": 1}

    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeEco())
    sid = sesiones.crear(root, [], "corte")
    for i in range(4):
        sesiones.agregar_mensaje(root, sid, "Diego" if i % 2 == 0 else "Asistente", f"m{i}")
    sesiones.actualizar_meta(root, sid, destilado_hasta=3)
    chat.turno({"hamuq": root}, sid, "nuevo")
    assert [m["content"] for m in visto["msgs"]] == ["m3", "nuevo"]
    assert isinstance(sesiones.cargar(root, sid)[0]["contexto_max"], int)


def test_turno_guarda_lo_escrito_aunque_el_proveedor_falle(root, monkeypatch):
    """El proveedor caído (sin crédito, sin conexión) no puede llevarse la pregunta:
    se guarda ANTES de llamar al modelo. Y el reintento manda el mismo texto, así
    que no debe quedar dos veces en el historial."""
    class Caido:
        def completar(self, system, msgs, tools=None):
            raise RuntimeError("claude CLI: Credit balance is too low")

    class Responde:
        def completar(self, system, msgs, tools=None):
            # el mensaje de Diego una sola vez; el fallo previo viaja como contexto
            assert [m["content"] for m in msgs].count("hola") == 1
            return {"texto": "ok", "tool_calls": [], "tokens_in": 5, "tokens_out": 1}

    monkeypatch.setattr(chat.servicios, "preparar", lambda *a, **k: None)  # sin tocar servicios reales
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: Caido())
    cfg = {"hamuq": root}
    sid = sesiones.crear(root, [], "caida")
    for n in (1, 2):                         # el fallo y su reintento
        with pytest.raises(RuntimeError):
            chat.turno(cfg, sid, "hola")
        msgs = sesiones.cargar(root, sid)[1]
        # la pregunta UNA vez (los fallos no cuentan como respuesta), y el fallo
        # queda escrito: traducido arriba, el crudo en el bloque plegado
        assert [m["texto"] for m in msgs].count("hola") == 1
        assert len(msgs) == 1 + n
        assert msgs[-1]["texto"].startswith("⚠ La cuenta de ese proveedor se quedó sin saldo.")
        assert msgs[-1]["texto"].endswith("Credit balance is too low")
        assert chat.MARCA_PROCESO in msgs[-1]["texto"]

    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: Responde())
    chat.turno(cfg, sid, "hola")             # reintento que sí anda
    msgs = sesiones.cargar(root, sid)[1]
    assert (msgs[0]["rol"], msgs[0]["texto"]) == ("Diego", "hola")
    # el texto visible sigue siendo "ok"; el registro plegado (ahora con la línea
    # ⏱ de cierre de todo turno, pedido 2026-08-09) va detrás del marcador
    assert msgs[-1]["rol"] == "Asistente"
    assert msgs[-1]["texto"].split(f"\n\n{chat.MARCA_PROCESO}")[0] == "ok"
    assert "⏱ turno" in msgs[-1]["texto"]
    assert len(msgs) == 4                    # la pregunta, dos fallos y la respuesta


def test_el_turno_deja_escrito_lo_que_hizo(root, monkeypatch):
    """Pedido 2026-08-07: lo que ocurrió durante el turno se guarda con la respuesta
    —plegado detrás de su marcador— y la pregunta de la nube y lo que Diego contestó
    quedan como texto visible, no como un diálogo que se desvanece."""
    from mem import crear

    class Genera:
        def __init__(self):
            self.n = 0

        def completar(self, system, msgs, tools=None, forzar_tool=""):
            self.n += 1
            if self.n == 1:
                return {"texto": "", "tokens_in": 3, "tokens_out": 1, "tool_calls": [
                    {"id": "1", "name": "crear_nube", "args": {"modelo": "veo3_1", "prompt": "a rocket"}}]}
            return {"texto": "listo", "tool_calls": [], "tokens_in": 4, "tokens_out": 1}

    monkeypatch.setattr(chat.servicios, "preparar", lambda *a, **k: None)
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: Genera())
    monkeypatch.setattr(crear, "ejecutar", lambda *a, **k: "![ok](/attach/x.mp4)")
    (root / "09_Sistema/Modos/media.md").write_text(
        "---\nnombre: media\nherramientas: [crear]\n---\nTaller.", encoding="utf-8")
    cfg = {"hamuq": root}
    sid = sesiones.crear(root, [], "taller", modo="media")

    texto, _, _ = chat.turno(cfg, sid, "generá un video de un cohete",
                             on_confirm=lambda n, a: {**a, "params": {"duration": "8"}})
    cuerpo, registro = texto.split(chat.MARCA_PROCESO)
    # la pregunta y la respuesta, visibles y arriba de todo
    assert "¿Generar `veo3_1` en la nube" in cuerpo and "**Sí, generar** · duration 8" in cuerpo
    assert "listo" in cuerpo
    # y el registro técnico, plegado: qué tool corrió y cómo salió
    assert "crear_nube · veo3_1" in registro and "ok" in registro
    # lo que se guardó en la sesión es exactamente eso
    assert sesiones.cargar(root, sid)[1][-1]["texto"] == texto


def test_error_del_cli_de_claude_se_lee(root):
    """El CLI escribe su error como JSON: la burbuja de error muestra el motivo,
    no 400 caracteres de JSON (reportado 2026-08-05)."""
    from mem import llm
    crudo = json.dumps({"type": "result", "is_error": True, "api_error_status": 400,
                        "result": "Credit balance is too low", "usage": {"input_tokens": 0}})
    assert llm._motivo_cli(crudo) == "Credit balance is too low"
    assert llm._motivo_cli("no es json") == "" and llm._motivo_cli("[1,2]") == ""


def test_contexto_max_conocido_o_cero():
    from mem import llm
    assert llm.contexto_max({"proveedor": "anthropic"}) == 200_000
    assert llm.contexto_max({"proveedor": "openai", "modelo": "x",
                             "base_url": "http://127.0.0.1:1/v1"}, timeout=0.3) == 0


def test_leer_medio_acepta_ruta_de_base_y_la_forma_attach(root):
    """El agente puede abrir un medio ya guardado; la ruta /attach/... que copia
    del markdown previo se normaliza sola."""
    (root / "07_Inbox/_adjuntos").mkdir(parents=True, exist_ok=True)
    (root / "07_Inbox/_adjuntos/notas.txt").write_text("tres vigas de 4m", encoding="utf-8")
    ctx = {"cfg": {"hamuq": root}}
    assert chat._ejecutar(root, "leer_medio", {"path": "/attach/07_Inbox/_adjuntos/notas.txt"},
                          [], 5, ctx) == "tres vigas de 4m"
    out = chat._ejecutar(root, "leer_medio", {"path": "no/existe.png"}, [], 5, ctx)
    assert "no está en la base" in out


def test_tope_de_paginas(root):
    paginas = ["a.md", "b.md"]
    out = chat._ejecutar(root, "leer_pagina",
                         {"path": "06_Biblioteca_Conocimiento/Entradas/gaussian-splatting.md"},
                         paginas, 2, {})
    assert "tope" in out


def test_lint_detecta_problemas(root):
    (root / "06_Biblioteca_Conocimiento/Entradas/huerfana.md").write_text("# Huérfana", encoding="utf-8")
    (root / "00_INDICE_GENERAL.md").write_text("[roto](no-existe.md)", encoding="utf-8")
    pap = root / "08_Categorias/_papelera"
    pap.mkdir(parents=True)
    (pap / "00_CATEGORIAS_2026-08-02.md").write_text("[copia vieja](no-existe.md)", encoding="utf-8")
    problemas = lint.correr(root)
    assert any("huerfana" in p for p in problemas)
    assert any("link roto" in p for p in problemas)
    assert not [p for p in problemas if "_papelera" in p]  # respaldos: sus links ya no resuelven


def test_lint_detecta_medios_descolgados(root):
    """Los dos lados del vínculo entrada ↔ archivo: la entrada que apunta a un
    png que ya no está, y el medio generado que nadie referencia."""
    adj = root / "07_Inbox/_adjuntos"
    adj.mkdir(parents=True)
    (adj / "suelta.png").write_bytes(b"x")
    (adj / "usada.png").write_bytes(b"x")
    memoria.capturar(root, "foto del museo", adjunto="07_Inbox/_adjuntos/usada.png")
    (root / "06_Biblioteca_Conocimiento/Entradas/rota.md").write_text(
        "---\ntitulo: rota\nadjunto: 07_Inbox/_adjuntos/borrada.png\n---\n# rota", encoding="utf-8")
    problemas = lint.correr(root)
    assert any("adjunto inexistente" in p and "borrada.png" in p for p in problemas)
    assert any("medio sin entrada" in p and "suelta.png" in p for p in problemas)
    assert not any("usada.png" in p for p in problemas)


def test_lint_duplicado_crear_crear_no_avisa(root):
    """Dos imágenes generadas del mismo prompt (origen=crear en las DOS) son
    variantes esperadas del taller de medios, no un duplicado real — no avisan."""
    contenido = "Generado con ComfyUI: un cohete de gominola despegando al atardecer."
    memoria.guardar_entrada(root, "Cohete uno", contenido, ["Creaciones"], origen="crear", tags=["comfyui"])
    memoria.guardar_entrada(root, "Cohete dos", contenido, ["Creaciones"], origen="crear", tags=["comfyui"])
    problemas = lint.correr(root)
    assert not [p for p in problemas if "posible duplicado" in p]


def test_lint_duplicado_mixto_avisa_con_fechas(root):
    """Un par MIXTO (uno crear, uno no) sí es sospechoso — y el aviso trae la
    fecha de cada lado, para saber cuándo se generó cada uno sin abrir nada."""
    contenido = "Generado con ComfyUI: un cohete de gominola despegando al atardecer."
    memoria.guardar_entrada(root, "Cohete uno", contenido, ["Creaciones"], origen="crear", tags=["comfyui"])
    memoria.guardar_entrada(root, "Cohete tres", contenido, ["Creaciones"], origen="inbox", tags=["comfyui"])
    problemas = lint.correr(root)
    dup = next(p for p in problemas if "posible duplicado" in p)
    assert re.search(r"\d{4}-\d{2}-\d{2} ≈ \d{4}-\d{2}-\d{2}", dup)
    assert "cohete-uno" in dup and "cohete-tres" in dup


def test_lint_ignorar_json_filtra_duplicados_y_medios(root):
    """`lint.ignorar` descarta de por vida (hasta que se edite el JSON a mano) —
    vive en el vault, no en localStorage, porque Diego usa la app desde varios
    dispositivos y un descarte tiene que valer en todos."""
    contenido = "Generado con ComfyUI: un cohete de gominola despegando al atardecer."
    memoria.guardar_entrada(root, "Cohete uno", contenido, ["Creaciones"], origen="crear", tags=["comfyui"])
    memoria.guardar_entrada(root, "Cohete tres", contenido, ["Creaciones"], origen="inbox", tags=["comfyui"])
    assert any("posible duplicado" in p for p in lint.correr(root))
    lint.ignorar(root, "duplicados", "cohete-uno|cohete-tres")
    assert not any("posible duplicado" in p for p in lint.correr(root))

    adj = root / "07_Inbox/_adjuntos"
    adj.mkdir(parents=True)
    (adj / "suelta.png").write_bytes(b"x")
    assert any("medio sin entrada" in p for p in lint.correr(root))
    lint.ignorar(root, "medios", "07_Inbox/_adjuntos/suelta.png")
    assert not any("medio sin entrada" in p for p in lint.correr(root))


def test_fusionar_entradas(root):
    """Fusionar = una absorbe a la otra: tags/subjects/enlaces unidos, adjunto
    heredado SOLO si la que absorbe no tenía uno, cuerpo de la absorbida anexado
    fechado, sus [[wikilinks]] de terceros redirigidos, y ella misma a la
    papelera (reversible — nunca borrado destructivo)."""
    adj = root / "07_Inbox/_adjuntos"
    adj.mkdir(parents=True)
    (adj / "foto.png").write_bytes(b"x")
    memoria.guardar_entrada(root, "Alfa", "Primera versión del mismo tema.", ["Casa"], tags=["a"])
    memoria.guardar_entrada(root, "Beta", "Segunda versión, casi idéntica.", ["Casa/Sub"], tags=["b"],
                            adjunto="07_Inbox/_adjuntos/foto.png", meta={"enlaces": ["https://x.test"]})
    memoria.guardar_entrada(root, "Gama", "Cita a [[beta]] para contexto.", ["Trabajo"])

    out = memoria.fusionar_entradas(root, "alfa", "beta")
    assert out == "fusionada: beta → alfa"

    viva = memoria.leer_entrada(root, "alfa")
    assert set(viva["tags"]) == {"a", "b"}
    assert "Casa/Sub" in viva["subjects"]
    assert viva["enlaces"] == ["https://x.test"]
    assert viva["adjunto"] == "07_Inbox/_adjuntos/foto.png"   # heredado: alfa no tenía uno propio
    assert "Segunda versión, casi idéntica." in viva["contenido"]
    # el slug de la absorbida queda como texto, NUNCA como [[wikilink]] — apuntaría
    # a un slug que ya no existe (bug real, encontrado al fusionar la base real)
    assert "`beta`" in viva["contenido"] and "[[beta]]" not in viva["contenido"]
    assert viva["version"] == 2

    with pytest.raises(FileNotFoundError):
        memoria.leer_entrada(root, "beta")
    assert (root / "06_Biblioteca_Conocimiento/_papelera/beta.md").exists()
    assert (adj / "foto.png").exists()   # con dueño vivo (alfa): no se mueve a papelera

    gama = (root / "06_Biblioteca_Conocimiento/Entradas/gama.md").read_text(encoding="utf-8")
    assert "[[alfa]]" in gama and "[[beta]]" not in gama

    with pytest.raises(ValueError):
        memoria.fusionar_entradas(root, "alfa", "alfa")
    with pytest.raises(FileNotFoundError):
        memoria.fusionar_entradas(root, "alfa", "no-existe")


def test_borrar_entrada_se_lleva_su_adjunto(root):
    """Borrar deja la base consistente: el archivo va a la papelera con su página
    y el lint queda limpio. Sin esto, cada sesión borrada "con sus memorias"
    dejaba un aviso "medio sin entrada" para siempre (2026-08-07).
    El compartido no se toca: el item del inbox procesado sigue nombrándolo."""
    adj = root / "07_Inbox/_adjuntos"
    adj.mkdir(parents=True)
    (adj / "sola.png").write_bytes(b"x")
    (adj / "compartida.png").write_bytes(b"x")
    memoria.capturar(root, "foto compartida", adjunto="07_Inbox/_adjuntos/compartida.png")
    for slug, a in (("sola", "sola.png"), ("compartida", "compartida.png")):
        memoria.guardar_entrada(root, slug, "x", ["Test"], adjunto=f"07_Inbox/_adjuntos/{a}")
        memoria.eliminar_entrada(root, slug)
    assert not (adj / "sola.png").exists()
    assert (root / memoria.ADJUNTOS_PAPELERA / "sola.png").is_file()
    assert (adj / "compartida.png").is_file(), "el item del inbox todavía la usa"
    assert not [p for p in lint.correr(root) if "adjunto" in p or "medio sin entrada" in p]


def test_papelera_adjunto_suelto(root):
    """El lado 'medio sin entrada' del lint y el borrado de sesión con contenido
    comparten esta función: manda a papelera un huérfano de _adjuntos/, y
    rechaza cualquier ruta que no sea un archivo plano ahí adentro — es la única
    barrera contra mover otra cosa del vault (2026-08-09)."""
    adj = root / "07_Inbox/_adjuntos"
    adj.mkdir(parents=True)
    (adj / "suelta.png").write_bytes(b"x")
    destino = memoria.papelera_adjunto_suelto(root, "07_Inbox/_adjuntos/suelta.png")
    assert destino == f"{memoria.ADJUNTOS_PAPELERA}/suelta.png"
    assert not (adj / "suelta.png").exists()
    assert (root / destino).is_file()

    with pytest.raises(FileNotFoundError):
        memoria.papelera_adjunto_suelto(root, "07_Inbox/_adjuntos/no-existe.png")
    with pytest.raises(ValueError):
        memoria.papelera_adjunto_suelto(root, "07_Inbox/_adjuntos/sub/otra.png")
    with pytest.raises(ValueError):
        memoria.papelera_adjunto_suelto(root, "09_Sistema/log.md")


def test_borrar_sesion_adjunto_exclusivo_a_papelera_compartido_no(root):
    """La misma decisión que toma DELETE /sessions/{sid}?contenido=1, probada
    directo sobre las piezas del motor (sin TestClient: api.py arma `cfg` real
    al importarse — de acá para arriba es todo lo que el endpoint hace)."""
    adj = root / "07_Inbox/_adjuntos"
    adj.mkdir(parents=True)
    (adj / "sola.png").write_bytes(b"x")
    (adj / "compartida.png").write_bytes(b"x")
    memoria.capturar(root, "foto compartida", adjunto="07_Inbox/_adjuntos/compartida.png")
    sid = sesiones.crear(root)
    sesiones.agregar_mensaje(root, sid, "Diego", "mirá esto ![](/attach/07_Inbox/_adjuntos/sola.png)")
    sesiones.agregar_mensaje(root, sid, "Diego", "y esto otro ![](/attach/07_Inbox/_adjuntos/compartida.png)")

    _, mensajes = sesiones.cargar(root, sid)
    rutas = dict.fromkeys(r for m in mensajes for r in chat.RX_ATTACH.findall(m["texto"]))
    sueltos = memoria.adjuntos_sueltos_de(root, sid, list(rutas))
    borrados = [memoria.papelera_adjunto_suelto(root, r) for r in sueltos]

    assert borrados == [f"{memoria.ADJUNTOS_PAPELERA}/sola.png"]
    assert not (adj / "sola.png").exists()
    assert (adj / "compartida.png").is_file(), "la memoria capturada todavía la nombra"


# ---------------------------------------------------------------- F2: deltas de backend

def test_sesiones_listar_archivadas(root):
    activa = sesiones.crear(root, ["IA"], "activa")
    archivada = sesiones.crear(root, ["IA"], "para archivar")
    sesiones.archivar(root, archivada)
    assert [m["id"] for m in sesiones.listar(root)] == [activa]
    assert [m["titulo"] for m in sesiones.listar(root, archivadas=True)] == ["para archivar"]


def test_destilar_sincroniza_al_inbox_y_recorta_el_contexto(root):
    sid = sesiones.crear(root, ["IA"], "sesión de prueba")
    sesiones.agregar_mensaje(root, sid, "Diego", "¿cuándo corre el lint?")
    sesiones.agregar_mensaje(root, sid, "Asistente", "después del proceso nocturno")
    r = chat.destilar({"hamuq": root}, sid)
    assert r == {"sincronizado": True, "estado": "pendiente", "inbox": f"07_Inbox/sesion-{sid}.md"}
    items = memoria.inbox_listar(root)
    assert len(items) == 1 and items[0]["id"] == f"sesion-{sid}"
    assert "¿cuándo corre el lint?" in items[0]["texto"] and "después del proceso nocturno" in items[0]["texto"]
    meta, _ = sesiones.cargar(root, sid)
    # el corte de contexto avanza igual que antes (lo único que este botón sigue
    # haciendo aparte de sincronizar — la promoción a la Biblioteca ya no es cosa suya)
    assert meta["destilado_hasta"] == 2
    assert meta["tokens_entrada_ultimo_turno"] == 0 and meta["tokens_historial_ultimo_turno"] == 0
    # llega material nuevo → vuelve a sincronizar TODO (upsert, no incremental) en el MISMO item
    sesiones.agregar_mensaje(root, sid, "Diego", "otra cosa más")
    chat.destilar({"hamuq": root}, sid)
    meta, _ = sesiones.cargar(root, sid)
    assert meta["destilado_hasta"] == 3
    items = memoria.inbox_listar(root)
    assert len(items) == 1 and "otra cosa más" in items[0]["texto"]

    vacia = sesiones.crear(root, [], "vacía")
    assert chat.destilar({"hamuq": root}, vacia) == {"sincronizado": False, "estado": ""}


def test_memorias_de_la_sesion_se_marcan_y_se_pueden_borrar_con_ella(root, monkeypatch):
    """El texto de la sesión sincroniza al inbox, no escribe la Biblioteca
    directo; recién cuando el pipeline normal la procesa queda marcada con su
    sesión de origen (la app la muestra como 'memoria de sesión' y linkea de
    vuelta), y borrar la sesión "con memorias" manda esa entrada a la papelera
    de la Biblioteca — nada destructivo."""
    sid = sesiones.crear(root, ["IA"], "sesión con memoria")
    sesiones.agregar_mensaje(root, sid, "Diego", "¿cuándo corre el lint?")
    sesiones.agregar_mensaje(root, sid, "Asistente", "después del proceso nocturno")
    chat.destilar({"hamuq": root}, sid)
    assert memoria.entradas_de_sesion(root, sid) == []   # todavía nada promovido, solo el inbox

    monkeypatch.setattr(procesar.llm_mod, "crear",
                        lambda cfg: FakeLLMProcesador(json.dumps({
                            "titulo": "Reglas de lint", "sintesis": "Corre tras el proceso nocturno.",
                            "tipo": "nota", "tags": [], "subjects": ["IA"]})))
    procesar.procesar_item({"hamuq": root}, root / "07_Inbox" / f"sesion-{sid}.md")

    assert memoria.entradas_de_sesion(root, sid) == [{"slug": "reglas-de-lint", "titulo": "Reglas de lint"}]
    porSlug = {e["slug"]: e for e in memoria.buscar_memorias(root)}
    assert porSlug["reglas-de-lint"]["sesion"] == sid          # la búsqueda expone el tipo "de sesión"
    assert memoria.entradas_de_sesion(root, "otra-sesion") == []

    papelera = memoria.eliminar_entrada(root, "reglas-de-lint")
    assert (root / papelera).is_file() and not (root / memoria.ENTRADAS / "reglas-de-lint.md").exists()
    assert "reglas-de-lint" not in [e["slug"] for e in memoria.buscar_memorias(root)]  # fuera de la Biblioteca
    # y fuera del índice por categoría (la línea cronológica se conserva: es historia)
    idx = (root / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md").read_text(encoding="utf-8")
    assert not [l for l in idx.splitlines() if l.lstrip().startswith("- [") and "reglas-de-lint" in l]
    assert "reglas-de-lint.md)" not in idx      # ni un link roto suelto en la cronológica
    assert not [p for p in lint.correr(root) if "link roto" in p]


def test_conexiones_backlinks(root):
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"])
    memoria.guardar_entrada(root, "Otra de IA", "Relacionada.", ["IA"])
    sid = sesiones.crear(root, ["IA"], "sesión que citó")
    sesiones.actualizar_meta(root, sid, paginas_usadas=[f"{memoria.ENTRADAS}/nota-ia.md"])
    con = memoria.conexiones(root, "nota-ia")
    assert con["sesiones"][0]["id"] == sid
    assert con["entradas"] == [{"slug": "otra-de-ia", "titulo": "Otra de IA"}]


def test_chat_tool_conexiones(root):
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"])
    memoria.guardar_entrada(root, "Otra de IA", "Relacionada.", ["IA"])
    out = chat._ejecutar(root, "conexiones", {"slug": "nota-ia"}, [], 5, {})
    assert json.loads(out)["entradas"] == [{"slug": "otra-de-ia", "titulo": "Otra de IA"}]
    assert chat._ejecutar(root, "conexiones", {"slug": "fantasma"}, [], 5, {}).startswith("(")
    # los modos sin `herramientas` explícitas la reciben del default
    (root / "09_Sistema/Modos/pelado.md").write_text("---\nnombre: pelado\n---\nHola.", encoding="utf-8")
    assert "conexiones" in modos.cargar(root, "pelado")["herramientas"]


class FakeLLMProcesador:
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.visto = ""          # lo que el procesador le mostró al modelo

    def completar(self, system, msgs, tools=None):
        self.visto = "\n".join(m["content"] for m in msgs)
        return {"texto": self.respuesta, "tool_calls": [], "tokens_in": 10, "tokens_out": 10}


def test_procesar_item_respeta_fijado_y_mueve_a_procesado(root, monkeypatch):
    monkeypatch.setattr(procesar.llm_mod, "crear",
                        lambda cfg: FakeLLMProcesador(json.dumps({
                            "titulo": "Idea de lint", "sintesis": "Reglas para tags huérfanos.",
                            "tipo": "tarea", "tags": ["ignorado"], "subjects": ["ignorado"]})))
    rel = memoria.capturar(root, "escribir reglas de lint", tags=["fijo"], subjects=["IA"])
    p = root / rel
    r = procesar.procesar_item({"hamuq": root}, p)
    assert r["estado"] == "procesada"
    assert not p.exists()
    procesado = root / "07_Inbox/_procesado" / p.name
    assert procesado.exists()
    meta = frontmatter.load(procesado).metadata
    assert meta["tags"] == ["fijo"] and meta["subjects"] == ["Proyectos/General", "IA"]  # fijado nunca se pisa
    entrada = (root / memoria.ENTRADAS / "idea-de-lint.md")
    assert entrada.exists()


def test_procesar_item_inyecta_subject_del_proyecto_de_la_sesion(root, monkeypatch):
    """Mismo criterio que tenía chat._ejecutar antes de que guardar_entrada se
    retirara del chat: la sesión con proyecto entra al proyecto sí o sí, aunque
    el clasificador no lo repita — y el chip "sesión de origen" sobrevive."""
    monkeypatch.setattr(procesar.llm_mod, "crear",
                        lambda cfg: FakeLLMProcesador(json.dumps({
                            "titulo": "Notas de la obra", "sintesis": "Avances de la semana.",
                            "tipo": "nota", "tags": [], "subjects": ["Casa"]})))  # sin el proyecto
    rel = memoria.sincronizar_sesion_inbox(root, "obra-1", "[Diego]\navances", proyecto="Casa nueva")
    r = procesar.procesar_item({"hamuq": root}, root / rel)
    entrada = frontmatter.load(root / r["entrada"]).metadata
    assert "Proyectos/Casa nueva" in entrada["subjects"]
    assert entrada["sesion"] == "obra-1"


def test_procesar_item_error_no_mueve_el_item(root, monkeypatch):
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: FakeLLMProcesador("esto no es json"))
    rel = memoria.capturar(root, "algo que fallará al procesar")
    p = root / rel
    with pytest.raises(ValueError):
        procesar.procesar_item({"hamuq": root}, p)
    assert p.exists()  # sigue en el inbox para reintentar


def test_procesar_item_extrae_enlaces_adjunto_lugar_y_fecha(root, monkeypatch):
    """Toda la información disponible entra al procesado: páginas enlazadas,
    adjunto, lugar y fecha/hora de la información — y queda indexada."""
    fake = FakeLLMProcesador(json.dumps({
        "titulo": "Charla de IA en el MALI", "sintesis": "Charla sobre agentes, entrada libre.",
        "tipo": "nota", "tags": ["evento"], "subjects": ["Tecnologia/IA", "Inmersivo"],
        "lugar": "MALI, Lima", "cuando": "2026-08-12T19:00"}))
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: fake)
    monkeypatch.setattr(procesar, "_leer_url", lambda url, timeout=8.0: f"pagina de {url}")
    (root / "07_Inbox/_adjuntos").mkdir(parents=True, exist_ok=True)
    (root / "07_Inbox/_adjuntos/programa.txt").write_text("19:00 apertura", encoding="utf-8")

    rel = memoria.capturar(root, "charla https://uno.test/a y también https://dos.test/b",
                           adjunto="07_Inbox/_adjuntos/programa.txt")
    r = procesar.procesar_item({"hamuq": root}, root / rel)

    assert "pagina de https://uno.test/a" in fake.visto and "pagina de https://dos.test/b" in fake.visto
    assert "19:00 apertura" in fake.visto  # el adjunto de texto también se lee
    meta = frontmatter.load(root / r["entrada"]).metadata
    assert meta["lugar"] == "MALI, Lima" and meta["cuando"] == "2026-08-12T19:00"
    assert meta["enlaces"] == ["https://uno.test/a", "https://dos.test/b"]
    assert meta["subjects"] == ["Tecnologia/IA", "Inmersivo", "Proyectos/General"]  # se cataloga en todas
    categorias = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    assert categorias.count("charla-de-ia-en-el-mali.md") == 3  # indexada bajo IA, Inmersivo y su proyecto
    assert not any(i["id"] == (root / rel).stem for i in memoria.inbox_listar(root))  # sale del inbox


def test_buscar_memorias_por_metadatos(root):
    # cuando= en un año lejano: así el filtro desde/hasta nunca choca con la
    # fecha de archivo de las otras entradas (que es la de HOY, sea cual sea)
    memoria.guardar_entrada(root, "Charla MALI", "Sobre agentes.", ["IA"], tags=["evento"],
                            meta={"lugar": "MALI, Lima", "cuando": "2031-08-12T19:00",
                                  "enlaces": ["https://uno.test/a"]})
    memoria.guardar_entrada(root, "Apuntes VR", "Sobre museos.", ["Inmersivo"])
    assert [m["slug"] for m in memoria.buscar_memorias(root, lugar="lima")] == ["charla-mali"]
    assert [m["slug"] for m in memoria.buscar_memorias(root, texto="evento")] == ["charla-mali"]
    assert [m["slug"] for m in memoria.buscar_memorias(root, texto="uno.test")] == ["charla-mali"]
    assert [m["slug"] for m in memoria.buscar_memorias(root, desde="2031-08-01", hasta="2031-08-31")] == ["charla-mali"]
    assert [m["slug"] for m in memoria.buscar_memorias(root, orden="titulo")] == [
        "apuntes-vr", "charla-mali", "gaussian-splatting"]  # la del fixture no tiene frontmatter
    assert memoria.buscar_memorias(root, texto="agentes")[0]["resumen"].startswith("Sobre agentes.")


def test_guardar_entrada_actualiza_metadatos_y_reindexa(root):
    memoria.guardar_entrada(root, "Charla MALI", "Primera nota.", ["IA"],
                            meta={"lugar": "MALI, Lima", "enlaces": ["https://uno.test/a"]})
    memoria.guardar_entrada(root, "Charla MALI", "Segunda nota.", ["Inmersivo"],
                            meta={"lugar": "otro", "enlaces": ["https://dos.test/b"]})
    meta = frontmatter.load(root / memoria.ENTRADAS / "charla-mali.md").metadata
    assert meta["lugar"] == "MALI, Lima"  # el escalar ya puesto no se pisa
    assert meta["enlaces"] == ["https://uno.test/a", "https://dos.test/b"]  # las listas se suman
    assert meta["subjects"] == ["Proyectos/General", "IA", "Inmersivo"]
    categorias = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    idx_inm = categorias.index("**Inmersivo**")
    assert "charla-mali.md" in categorias[idx_inm:]  # el subject nuevo también quedó indexado


def test_procesar_inbox_marca_error_y_sigue(root, monkeypatch):
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: FakeLLMProcesador("no-json"))
    memoria.capturar(root, "una captura cualquiera")
    r = procesar.procesar_inbox({"hamuq": root})
    assert r["procesadas"] == 0 and r["errores"] == 1
    pendiente = next((root / "07_Inbox").glob("*.md"))
    meta = frontmatter.load(pendiente).metadata
    assert meta["estado"] == "error" and "error" in meta


def test_primer_turno_pasa_la_temporal_a_activa(root, monkeypatch):
    """El botón ⌸ Guardar se quitó (2026-08-06): conversar YA la guarda. Sin esto
    las temporales quedaban invisibles en Think para siempre.

    Y pasa a activa CON el mensaje, no al terminar el turno: mientras el modelo
    piensa —22 min con una generación de por medio, 2026-08-23— la sesión tiene
    que estar en /sessions, o desde otro aparato no hay forma de encontrarla ni
    de contestarle el diálogo de la nube."""
    visto = []
    class FakeLLMQueMira(FakeLLM):
        def completar(self, system, msgs, tools=None):
            visto.append([m["id"] for m in sesiones.listar(root)])
            return super().completar(system, msgs, tools)
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: FakeLLMQueMira())
    sid = sesiones.crear(root, [], "sesion", estado="temporal")
    assert sesiones.listar(root) == []
    chat.turno({"hamuq": root}, sid, "¿qué hay de captura volumétrica?")
    assert visto and all(v == [sid] for v in visto), visto   # visible ya en el primer paso
    assert [m["id"] for m in sesiones.listar(root)] == [sid]
    meta, _ = sesiones.cargar(root, sid)
    assert meta["estado"] == "activa"


def test_indexar_categorias_crea_en_su_grupo(root):
    """Un tema nuevo va a su grupo real (creándolo si falta) — nunca a '_Nuevas'."""
    memoria.guardar_entrada(root, "Nota video", "Sobre video.", ["Tecnologia/Video", "Salud"])
    cat = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    assert "_Nuevas" not in cat
    assert cat.index("**Video**") > cat.index("## Tecnologia")  # hoja nueva en su grupo
    assert "## Salud" in cat                                     # grupo nuevo de un nivel
    arbol = memoria.arbol_subjects(root)
    tec = next(n for n in arbol if n["nombre"] == "Tecnologia")
    assert next(h for h in tec["hijos"] if h["nombre"] == "Video")["entradas"] == 1
    assert next(n for n in arbol if n["nombre"] == "Salud")["entradas"] == 1


def test_reconstruir_indices_desde_frontmatter(root):
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["Tecnologia/IA"])
    memoria.guardar_entrada(root, "Nota rara", "Contenido.", ["Cine/Documental"])
    # árbol ensuciado a mano, como una base vieja con la sección _Nuevas
    (root / "08_Categorias/00_CATEGORIAS.md").write_text(
        "# Categorías\n\n## _Nuevas (revisar)\n- **Cine/Documental** — creada automáticamente\n", encoding="utf-8")
    r = memoria.reconstruir_indices(root)
    cat = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    assert "_Nuevas" not in cat and "## Cine" in cat and "**Documental**" in cat
    assert "nota-ia.md" in cat
    assert list((root / "08_Categorias/_papelera").glob("00_CATEGORIAS_*.md"))  # respaldo, nada se borra
    tematico = (root / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md").read_text(encoding="utf-8")
    assert "### Tecnologia/IA" in tematico and "### Cine/Documental" in tematico
    assert "## Todas las entradas (orden cronológico)" in tematico  # la historia se conserva
    assert "entradas indexadas" in r


class FakeLLMReorg:
    """Guion fijo: primero propone el árbol, después asigna el mismo tema a todo."""
    def __init__(self):
        self.paso = 0

    def completar(self, system, msgs, tools=None):
        self.paso += 1
        if self.paso == 1:
            return {"texto": json.dumps({"arbol": ["Tecnologia/IA", "Cultura/Museos"]}),
                    "tool_calls": [], "tokens_in": 1, "tokens_out": 1}
        return {"texto": json.dumps({"subjects": ["Cultura/Museos"]}),
                "tool_calls": [], "tokens_in": 1, "tokens_out": 1}


def test_reorganizar_planear_y_aplicar(root, monkeypatch):
    from mem import reorganizar
    monkeypatch.setattr(reorganizar.llm_mod, "crear", lambda ag: FakeLLMReorg())
    memoria.guardar_entrada(root, "Museo VR", "Recorrido con gaussian splatting.", ["IA"])
    plan = reorganizar.planear({"hamuq": root})
    cambio = next(c for c in plan["cambios"] if c["slug"] == "museo-vr")
    # el proyecto no se reorganiza: es dónde vive la memoria, no un tema suyo
    assert cambio["antes"] == ["Proyectos/General", "IA"]
    assert cambio["despues"] == ["Proyectos/General", "Cultura/Museos"]
    r = reorganizar.aplicar({"hamuq": root}, plan)
    assert r["cambiadas"] >= 1
    meta = frontmatter.load(root / memoria.ENTRADAS / "museo-vr.md").metadata
    assert meta["subjects"] == ["Proyectos/General", "Cultura/Museos"]
    texto = (root / memoria.ENTRADAS / "museo-vr.md").read_text(encoding="utf-8")
    assert "(reorganización)" in texto  # cambio fechado en el registro histórico
    cat = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    assert "## Cultura" in cat and "museo-vr.md" in cat


def test_reorganizar_sin_llm_no_cambia_nada(root, monkeypatch):
    from mem import reorganizar

    def caido(ag):
        raise RuntimeError("proveedor caído")
    monkeypatch.setattr(reorganizar.llm_mod, "crear", caido)
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"])
    plan = reorganizar.planear({"hamuq": root})
    assert all(c["despues"] == c["antes"] for c in plan["cambios"])  # cero pérdida


def test_config_actualizar_preserva_comentarios(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text(
        "# comentario\nhamuq = 'W:\\x'\n\nproveedor = \"openai\"   # nota\nmodelo = \"x\"\n", encoding="utf-8")
    cfg = {"proveedor": "openai", "modelo": "x", "hamuq": tmp_path}
    config.actualizar(cfg, {"proveedor": "anthropic", "modelo": "claude-sonnet-5"}, path=str(toml))
    texto = toml.read_text(encoding="utf-8")
    assert "# comentario" in texto and 'proveedor = "anthropic"' in texto
    assert cfg["proveedor"] == "anthropic" and cfg["modelo"] == "claude-sonnet-5"
    # ruta Windows: se escribe como literal TOML (comillas simples, backslashes intactos)
    config.actualizar(cfg, {"hamuq": r"W:\otra\base"}, path=str(toml))
    import tomllib
    assert tomllib.loads(toml.read_text(encoding="utf-8"))["hamuq"] == r"W:\otra\base"
    assert cfg["hamuq"] == Path(r"W:\otra\base")  # vuelve a ser Path en memoria
    # un número va sin comillas: si se guarda como texto, al reiniciar vuelve str
    config.actualizar(cfg, {"idle_minutos": 30}, path=str(toml))
    assert tomllib.loads(toml.read_text(encoding="utf-8"))["idle_minutos"] == 30


def test_config_estilos_guardados_round_trip(tmp_path):
    """Lista de dicts → array/tabla inline TOML en una sola línea (actualizar()
    reemplaza por regex de línea completa). Nombres con comillas o apóstrofes:
    el criterio viejo de _literal (comilla simple si no hay backslash) rompía
    el TOML apenas el nombre traía una comilla doble (bug real, 2026-08-09)."""
    import tomllib

    toml = tmp_path / "config.toml"
    toml.write_text("proveedor = \"anthropic\"\n", encoding="utf-8")
    cfg = config.cargar(str(toml))
    assert cfg["estilos_guardados"] == []

    estilos = [
        {"nombre": "A", "palette": "sage", "themePref": "light", "burbuja": "llena",
         "radio": "recto", "borde": "normal", "fuente": "archivo"},
        {"nombre": 'B "rara"', "palette": "tinta", "themePref": "dark", "burbuja": "contorno",
         "radio": "suave", "borde": "sutil", "fuente": "inter"},
        {"nombre": "C con ' apóstrofe", "palette": "grafito", "themePref": "auto", "burbuja": "minima",
         "radio": "redondo", "borde": "marcado", "fuente": "serif"},
    ]
    cfg = config.actualizar(cfg, {"estilos_guardados": estilos}, path=str(toml))
    assert cfg["estilos_guardados"] == estilos
    # y de nuevo desde cero, releyendo el archivo escrito — el round-trip real
    assert tomllib.loads(toml.read_text(encoding="utf-8"))["estilos_guardados"] == estilos
    assert config.cargar(str(toml))["estilos_guardados"] == estilos


def test_vigilante_ocioso_descarga_una_vez(monkeypatch):
    """La descarga por inactividad, sin esperar 15 min ni tocar servicios reales
    (`lms unload --all` descargaría los modelos de verdad)."""
    from unittest import mock

    from mem import servicios

    cfg = {"idle_minutos": 15}
    cargados = [{"servicio": "LM Studio", "nombre": "qwen"}]
    monkeypatch.setattr(servicios.recursos, "modelos", lambda _cfg: cargados)
    monkeypatch.setattr(servicios, "activo", lambda _cfg, _n: False)   # ComfyUI apagado
    descargar = mock.Mock(return_value=(True, "ok"))
    monkeypatch.setattr(servicios, "descargar_modelos", descargar)

    servicios.marcar_uso()
    assert servicios.revisar_ocioso(cfg) == ""            # recién usado: no toca nada
    servicios._ultimo_uso -= 16 * 60
    assert servicios.revisar_ocioso(cfg) == "LM Studio"
    assert servicios.revisar_ocioso(cfg) == ""            # una vez por tramo ocioso
    assert descargar.call_count == 1

    # ComfyUI generando ES uso, aunque no venga de MeM: reinicia el reloj
    cargados[:] = [{"servicio": "ComfyUI", "nombre": "generando"}]
    servicios._ultimo_uso -= 16 * 60
    assert servicios.revisar_ocioso(cfg) == "" and descargar.call_count == 1
    assert servicios.revisar_ocioso({"idle_minutos": 0}) == ""   # apagado
    servicios.marcar_uso()


def test_config_sanitizado_oculta_token():
    vista = config.sanitizado({"proveedor": "openai", "modelo": "m", "base_url": "u",
                               "api_key_env": "X", "hamuq": "W:\\x", "puerto": 8765, "token": "secreto1234"})
    assert vista["token"] == "•••• 1234" and "secreto" not in vista["token"]


def test_agentes_base_no_borrables_y_asignacion(tmp_path, monkeypatch):
    monkeypatch.setenv("MEM_CONFIG", str(tmp_path / "config.toml"))
    cfg = {"proveedor": "openai", "modelo": "local-x", "base_url": "http://l:1234/v1", "api_key_env": ""}
    # sin agentes.json: bootstrap = exactamente los dos base (Smart, Cheap)
    data = agentes.cargar(cfg)
    assert [a["id"] for a in data["agentes"]] == ["smart", "cheap"]
    assert agentes.para(cfg, "chat")["modelo"] == "claude-sonnet-5"       # sin asignación → primero (smart)

    # agrega un agente custom y asigna "cheap" a procesar
    data["agentes"].append({"id": "local", "nombre": "Local", "icono": "◉", "proveedor": "openai",
                            "modelo": "m1", "base_url": "http://l:1234/v1", "api_key_env": "", "system_prompt": ""})
    data["asignaciones"] = {"procesar": "cheap"}
    agentes.guardar(data)
    assert agentes.para(cfg, "procesar")["modelo"] == "claude-haiku-4-5-20251001"
    assert agentes.para(cfg, "chat", agente_id="local")["modelo"] == "m1"
    assert agentes.para(cfg, "resumir", agente_id="no-existe")["modelo"] == "claude-sonnet-5"  # id roto → primero

    # guardar sin los agentes base: rechazado
    sin_base = [a for a in data["agentes"] if a["id"] not in agentes.BASE_IDS]
    with pytest.raises(ValueError):
        agentes.guardar({"agentes": sin_base, "asignaciones": {}})

    # cargar() reinyecta los base si un agentes.json viejo/editado a mano los perdió
    (tmp_path / "config.toml").with_name("agentes.json").write_text(
        json.dumps({"agentes": sin_base, "asignaciones": {}}), encoding="utf-8")
    migrada = agentes.cargar(cfg)
    assert {a["id"] for a in migrada["agentes"]} == {"local", "smart", "cheap"}


def test_proyectos_son_subjects_y_la_sesion_los_fija(root):
    memoria.proyecto_guardar(root, "Casa nueva", False)
    memoria.proyecto_guardar(root, "Cliente X", False)
    memoria.proyecto_guardar(root, "casa nueva", True)          # mismo proyecto: cambia privado
    lista = memoria.proyectos_listar(root)
    assert [p["nombre"] for p in lista] == ["Casa nueva", "Cliente X"]
    assert lista[0]["privado"] is True
    assert memoria.proyecto_por_nombre(root, "CASA NUEVA")["nombre"] == "Casa nueva"
    with pytest.raises(ValueError):
        memoria.proyecto_guardar(root, "", True)   # sin nombre

    # una sesión con proyecto guarda dentro del proyecto aunque el modelo no lo ponga
    # (ver test_procesar_item_inyecta_subject_del_proyecto_de_la_sesion para el
    # camino real hoy: chat ya no llama guardar_entrada directo, sincroniza al
    # inbox y es procesar_item quien inyecta el subject del proyecto)
    sid = sesiones.crear(root, [], "obra", proyecto="Casa nueva")
    ctx = {"sid": sid, "proyecto": "Casa nueva"}

    # el agente puede cambiarlo desde el chat; lo que se guarde después va al nuevo
    chat._ejecutar(root, "fijar_proyecto", {"nombre": "Cliente X"}, [], 5, ctx)
    assert sesiones.cargar(root, sid)[0]["proyecto"] == "Cliente X"
    assert ctx["proyecto"] == "Cliente X"


def test_proyecto_renombrar_y_eliminar(root):
    memoria.proyecto_guardar(root, "Casa", False)
    memoria.proyecto_guardar(root, "CasaNueva", False)   # prefijo del otro: no debe arrastrarse
    sid = sesiones.crear(root, [], "obra", proyecto="Casa")
    memoria.guardar_entrada(root, "Presupuesto", "3200 soles.", ["Proyectos/Casa"])
    memoria.guardar_entrada(root, "Otra obra", "cemento.", ["Proyectos/CasaNueva"])

    # renombrar mueve el subject de SUS memorias y el campo de SUS sesiones
    memoria.proyecto_renombrar(root, "casa", "Casa Playa")
    for sid2 in sesiones.de_proyecto(root, "Casa"):
        sesiones.actualizar_meta(root, sid2, proyecto="Casa Playa")
    assert sesiones.cargar(root, sid)[0]["proyecto"] == "Casa Playa"
    assert {p["nombre"] for p in memoria.proyectos_listar(root)} == {"Casa Playa", "CasaNueva"}
    assert memoria.leer_entrada(root, "presupuesto")["subjects"] == ["Proyectos/Casa Playa"]
    assert memoria.leer_entrada(root, "otra-obra")["subjects"] == ["Proyectos/CasaNueva"]
    with pytest.raises(ValueError):
        memoria.proyecto_renombrar(root, "Casa Playa", "casanueva")   # nombre ya tomado

    # SOLO: el proyecto se va y su memoria cae en General (suelto ya no es un lugar)
    out = memoria.proyecto_eliminar(root, "Casa Playa")
    assert out["memorias"] == ["presupuesto"]
    assert memoria.leer_entrada(root, "presupuesto")["subjects"] == ["Proyectos/General"]
    assert {p["nombre"] for p in memoria.proyectos_listar(root)} == {"CasaNueva", "General"}

    # General es fijo: no se borra, no se renombra, no se une, no se hace privado.
    # Su CONTENIDO sí se mueve (es a dónde caen las huérfanas, dos líneas arriba).
    for llamada in (lambda: memoria.proyecto_eliminar(root, "General"),
                    lambda: memoria.proyecto_renombrar(root, "General", "Otro"),
                    lambda: memoria.proyecto_renombrar(root, "general", "CasaNueva", fusionar=True),
                    lambda: memoria.proyecto_guardar(root, "General", privado=True)):
        with pytest.raises(ValueError):
            llamada()
    assert memoria.leer_entrada(root, "presupuesto")["subjects"] == ["Proyectos/General"]
    memoria.editar_entrada(root, "presupuesto", subjects=["Proyectos/CasaNueva"])
    assert memoria.leer_entrada(root, "presupuesto")["subjects"] == ["Proyectos/CasaNueva"],         "el candado es del proyecto, no de sus memorias"

    # TODO: la memoria va a la papelera con el proyecto
    memoria.proyecto_eliminar(root, "CasaNueva", con_contenido=True)
    assert not (root / memoria.ENTRADAS / "otra-obra.md").exists()
    assert (root / "06_Biblioteca_Conocimiento/_papelera/otra-obra.md").exists()
    with pytest.raises(FileNotFoundError):
        memoria.proyecto_eliminar(root, "CasaNueva")


def test_adjunto_no_soportado_deja_la_memoria_pendiente_y_se_reprocesa(root, monkeypatch):
    """Un video que el pipeline no puede leer deja la entrada marcada con QUÉ
    faltó; al poder leerlo, el reproceso mete su contenido EN la memoria, arregla
    el título que había quedado mal y todo eso se vuelve buscable."""
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: FakeLLMProcesador(json.dumps({
        "titulo": "Adjunto no leído", "sintesis": "Video de la obra.", "tipo": "nota",
        "tags": [], "subjects": ["Inmersivo"]})))
    monkeypatch.setattr(media, "extraer", lambda ag, rt, adj: ("", "obra.mp4: video sin fotogramas", True))
    (root / "07_Inbox/_adjuntos").mkdir(parents=True, exist_ok=True)
    (root / "07_Inbox/_adjuntos/obra.mp4").write_bytes(b"\x00binario")
    rel = memoria.capturar(root, "el techo quedó así", adjunto="07_Inbox/_adjuntos/obra.mp4")

    r = procesar.procesar_item({"hamuq": root}, root / rel)
    assert len(r["pendiente"]) == 1 and "video" in r["pendiente"][0]
    entrada = frontmatter.load(root / r["entrada"]).metadata
    assert "obra.mp4" in entrada["pendiente"][0]
    assert memoria.buscar_memorias(root, texto="obra")[0]["pendiente"]  # Memory lo puede pintar en rojo

    # cambio de modelo: ahora sí ve el video → el reproceso limpia la marca. El
    # modelo nuevo titula distinto: se actualiza LA MISMA entrada, no una gemela
    # que dejaría la original pendiente para siempre.
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: FakeLLMProcesador(json.dumps({
        "titulo": "Techo de la obra ya terminado", "sintesis": "Se ve el techo con las vigas puestas.",
        "tipo": "nota", "tags": [], "subjects": ["Tecnologia/Inmersivo"]})))
    monkeypatch.setattr(media, "extraer", lambda ag, rt, adj: (
        "[0s] Vigas de madera sobre el muro; un cartel dice FERRETERIA SUR.", "", True))
    out = procesar.reprocesar({"hamuq": root})
    assert out == {"procesadas": 1, "errores": 0, "detalle_errores": [], "pendientes": 0}
    assert [p.stem for p in (root / memoria.ENTRADAS).glob("*.md")] == ["adjunto-no-leido", "gaussian-splatting"]
    entrada = frontmatter.load(root / r["entrada"])
    assert "pendiente" not in entrada.metadata
    assert "vigas puestas" in entrada.content            # lo que faltaba entró como línea fechada
    # el título malo del primer pase no se queda pegado
    assert entrada.metadata["titulo"] == "Techo de la obra ya terminado"
    assert entrada.content.lstrip().startswith("# Techo de la obra ya terminado")
    # ni los temas: eran una conjetura sobre un adjunto que no se pudo leer
    assert entrada.metadata["subjects"] == ["Tecnologia/Inmersivo", "Proyectos/General"]
    # y el árbol queda con UN link, con el título nuevo, bajo el tema nuevo
    cats = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    assert cats.count("adjunto-no-leido.md") == 2   # el tema nuevo y su proyecto
    assert "[Techo de la obra ya terminado]" in cats
    # ni el índice cronológico —que se conserva— sigue diciendo el título viejo
    idx = (root / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md").read_text(encoding="utf-8")
    assert "Adjunto no leído" not in idx and "[Techo de la obra ya terminado]" in idx
    # y lo que se VE en el video quedó como texto dentro de la memoria...
    assert "FERRETERIA SUR" in entrada.content
    assert memoria.resumen_corto(entrada.content).startswith("Se ve el techo")  # sin taparle la ficha
    # ...así que se encuentra por su contenido, no solo por el nombre del archivo
    assert "FERRETERIA SUR" in memoria.buscar(root, "ferreteria")
    assert memoria.buscar_memorias(root, texto="ferreteria")[0]["slug"] == "adjunto-no-leido"
    assert not memoria.inbox_listar(root)  # el item volvió a _procesado, no quedó suelto


def test_video_largo_va_entero_al_cuerpo_y_condensado_al_prompt(root, monkeypatch):
    """Una hora de video deja una linea de tiempo que no entra en un pedido al
    modelo. Al prompt va resumida; al cuerpo de la memoria va ENTERA, que es la
    unica copia de lo que paso ahi y lo que la hace buscable despues."""
    fake = FakeLLMProcesador(json.dumps({
        "titulo": "Charla larga", "sintesis": "Hablo una hora.", "tipo": "nota",
        "tags": [], "subjects": ["Inmersivo"]}))
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: fake)
    linea = chr(10).join(f"[{m:02d}:00] hablado del minuto {m} con el pajaro" for m in range(1200))
    assert len(linea) > 3 * media.MAX_CHARS
    monkeypatch.setattr(media, "extraer", lambda ag, rt, adj: (linea, "", True))
    monkeypatch.setattr(media, "condensar",
                        lambda cfg, t: "RESUMEN por tramos" if len(t) > media.MAX_CHARS else t)
    (root / "07_Inbox/_adjuntos").mkdir(parents=True, exist_ok=True)
    (root / "07_Inbox/_adjuntos/charla.mp4").write_bytes(b"video")
    rel = memoria.capturar(root, "la charla", adjunto="07_Inbox/_adjuntos/charla.mp4")

    r = procesar.procesar_item({"hamuq": root}, root / rel)
    entrada = frontmatter.load(root / r["entrada"])
    assert "RESUMEN por tramos" in fake.visto and "[19:00]" not in fake.visto   # al modelo, resumido
    assert entrada.content.count("con el pajaro") == 1200                       # al cuerpo, entera
    assert len(entrada.content) > 3 * media.MAX_CHARS
    # y se lee por partes, que es como el chat vuelve a la transcripcion completa
    pag = memoria.leer_pagina(root, r["entrada"], parte=2)
    assert "parte 2/" in pag and "[500:00]" in pag   # la parte 2 trae el medio del video


def test_youtube_se_mira_y_se_tira_sin_dejar_el_video_en_la_base(root, monkeypatch):
    """Un link de YouTube se procesa bajando el video a un temporal: la memoria se
    queda con la linea de tiempo y el link, no con 200 MB de mp4."""
    fake = FakeLLMProcesador(json.dumps({
        "titulo": "El pajaro del video", "sintesis": "Mostro un pajaro.", "tipo": "link",
        "tags": [], "subjects": ["Naturaleza"]}))
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: fake)

    def _bajar(url, destino):
        (Path(destino) / "abc.mp4").write_bytes(b"video")
        return Path(destino) / "abc.mp4", "Video de YouTube: Aves" + chr(10) + "Canal: Nat"
    monkeypatch.setattr(youtube, "bajar", _bajar)
    monkeypatch.setattr(media, "describir_video", lambda cfg, p: (
        "[00:12] mira esto" + chr(10) * 2 + "[00:13] un pajaro en una rama", ""))
    # la pagina de YouTube no se busca: no dice nada del video
    monkeypatch.setattr(procesar, "_leer_url", lambda url, timeout=8.0: 1 / 0)
    rel = memoria.capturar(root, "https://youtu.be/abc mira", tipo="link")

    r = procesar.procesar_item({"hamuq": root}, root / rel)
    entrada = frontmatter.load(root / r["entrada"])
    assert entrada.metadata["adjunto"] == ""                        # el mp4 no entro a la base
    assert entrada.metadata["enlaces"] == ["https://youtu.be/abc"]  # el link si: es a lo que se vuelve
    assert "un pajaro en una rama" in entrada.content               # la linea de tiempo, en el cuerpo
    assert "Canal: Nat" in fake.visto                               # la ficha, en el prompt
    assert not list((root / "07_Inbox/_adjuntos").glob("*.mp4"))
    assert memoria.buscar_memorias(root, texto="rama")


def test_youtube_que_pide_cuenta_deja_la_memoria_pendiente(root, monkeypatch):
    """Si el video no es publico la captura no se pierde: la entrada se crea igual
    y queda marcada con el motivo, como cualquier adjunto ilegible."""
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: FakeLLMProcesador(json.dumps({
        "titulo": "Link sin leer", "sintesis": "Un video de YouTube.", "tipo": "link",
        "tags": [], "subjects": ["Inmersivo"]})))

    def _bajar(url, destino):
        raise RuntimeError("YouTube pide una cuenta para ese video y MeM solo lee publicos")
    monkeypatch.setattr(youtube, "bajar", _bajar)
    rel = memoria.capturar(root, "https://www.youtube.com/watch?v=priv", tipo="link")

    r = procesar.procesar_item({"hamuq": root}, root / rel)
    assert len(r["pendiente"]) == 1 and "cuenta" in r["pendiente"][0]
    assert frontmatter.load(root / r["entrada"]).metadata["enlaces"] == ["https://www.youtube.com/watch?v=priv"]


class FakeLLMNube:
    """Pide una generación en la nube y después cuenta cómo salió."""
    def __init__(self):
        self.resultados = []

    def completar(self, system, msgs, tools=None, forzar_tool=""):
        if not self.resultados:
            return {"texto": "", "tool_calls": [{"id": "n1", "name": "crear_nube",
                    "args": {"modelo": "soul", "prompt": "a rocket"}}],
                    "tokens_in": 10, "tokens_out": 5}
        return {"texto": "listo", "tool_calls": [], "tokens_in": 5, "tokens_out": 5}


def _turno_nube(root, monkeypatch, on_confirm):
    """Un turno del modo media que pide crear_nube; devuelve lo que la tool contestó."""
    from mem import crear
    fake = FakeLLMNube()
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: fake)
    ejecutadas = []
    monkeypatch.setattr(crear, "ejecutar",
                        lambda *a, **k: ejecutadas.append(a[1]) or "![ok](/attach/x.png)")
    modo = {"prompt": "taller", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    chat.responder({"hamuq": root}, modo, [{"role": "user", "content": "hacelo en la nube"}],
                   on_result=lambda n, a, salida: fake.resultados.append(salida),
                   on_confirm=on_confirm)
    return fake.resultados[0], ejecutadas


def test_crear_nube_cancelada_no_gasta_creditos(root, monkeypatch):
    """El diálogo de la app dijo que no: la tool NUNCA se ejecuta y el modelo se
    entera por el resultado (pedido 2026-08-06). El diálogo contesta con los args
    a usar; cualquier otra cosa —None, un bool de un cliente viejo— es un no, que
    es el único default aceptable cuando del otro lado hay plata."""
    for respuesta in (None, False, True):
        salida, ejecutadas = _turno_nube(root, monkeypatch, lambda nombre, args: respuesta)
        assert ejecutadas == [], respuesta        # lo que importa: no se gastó nada
        assert "NO autorizó" in salida


def test_crear_nube_confirmada_se_ejecuta(root, monkeypatch):
    salida, ejecutadas = _turno_nube(root, monkeypatch, lambda nombre, args: args)
    assert ejecutadas == ["crear_nube"]
    assert salida.startswith("![ok]")


def test_decir_no_a_la_nube_y_hacerlo_en_local_es_una_sola_respuesta(root, monkeypatch):
    """La tercera opción del diálogo (pedido 2026-08-07). Decir que no y después
    escribir "hacelo con ComfyUI" eran dos mensajes para lo mismo: ahora el "no"
    ya lleva la orden de generar en local, y el modelo la recibe como resultado
    de la tool. Sigue sin gastar un crédito."""
    from mem import crear
    salida, ejecutadas = _turno_nube(root, monkeypatch, lambda nombre, args: crear.RECHAZO_LOCAL)
    assert ejecutadas == []
    assert "crear_imagen" in salida and "EN LOCAL" in salida


class FakeMediaSecuencia:
    """Llama a las tools que le digan, una por vuelta, y después contesta."""
    def __init__(self, *llamadas):
        self.llamadas = list(llamadas)

    def completar(self, system, msgs, tools=None, forzar_tool=""):
        if self.llamadas:
            nombre, args = self.llamadas.pop(0)
            return {"texto": "", "tokens_in": 5, "tokens_out": 1,
                    "tool_calls": [{"id": str(len(self.llamadas)), "name": nombre, "args": args}]}
        return {"texto": "listo", "tool_calls": [], "tokens_in": 5, "tokens_out": 1}


def _turno_media(root, monkeypatch, cfg_extra, fake, on_confirm, pedido="generá un video"):
    """Un turno del taller con Ajustes de verdad; devuelve [(tool, args)] ejecutadas."""
    from mem import crear
    monkeypatch.setattr(chat.llm_mod, "crear", lambda cfg: fake)
    hechas = []
    monkeypatch.setattr(crear, "ejecutar",
                        lambda cfg, nombre, args, **kw: hechas.append((nombre, args)) or "![ok](/attach/x.mp4)")
    modo = {"prompt": "taller", "herramientas": ["crear"], "max_paginas": 5, "retrieval": "agentic"}
    chat.responder({"hamuq": root, **cfg_extra}, modo, [{"role": "user", "content": pedido}],
                   on_confirm=on_confirm)
    return hechas


def test_higgsfield_sin_job_type_pregunta_cual_y_no_cae_al_local(root, monkeypatch):
    """El bug del 2026-08-07: Ajustes › Media decía nube para video, Diego pidió
    "generá un video en higgsfield" y salió por ComfyUI. Sin job_type por defecto
    no se adivina un modelo de pago —pero tampoco se genera gratis en local—: se
    pregunta cuál en el MISMO diálogo que ya autoriza el gasto."""
    preguntas = []

    def dialogo(nombre, args):
        preguntas.append(args)
        return {**args, "modelo": "veo3_1", "params": {"duration": "4"}}   # lo elige Diego ahí

    hechas = _turno_media(root, monkeypatch, {"media_video": "higgsfield", "higgs_video": ""},
                          FakeMediaSecuencia(("crear_video", {"prompt": "x"})), dialogo,
                          pedido="generá un video en higgsfield")
    assert [n for n, _ in hechas] == ["crear_nube"]        # nunca tocó ComfyUI
    assert preguntas and preguntas[0]["modelo"] == ""      # el diálogo pregunta cuál
    assert hechas[0][1]["modelo"] == "veo3_1"              # y genera con el elegido


def test_no_a_la_nube_y_en_local_no_rebota_contra_el_ajuste(root, monkeypatch):
    """Con Ajustes en nube, contestar "no, hacelo en local" tiene que quedar firme
    por el resto del turno: si no, el enrutado vuelve a leer Ajustes y el turno
    hace ping-pong entre el diálogo y la nube sin generar nada."""
    from mem import crear
    fake = FakeMediaSecuencia(("crear_video", {"prompt": "x"}), ("crear_video", {"prompt": "x"}))
    hechas = _turno_media(root, monkeypatch, {"media_video": "higgsfield", "higgs_video": "veo3_1"},
                          fake, lambda n, a: crear.RECHAZO_LOCAL)
    assert [n for n, _ in hechas] == ["crear_video"]       # la segunda vuelta sí generó, y en local


def test_crear_nube_sin_dialogo_se_ejecuta(root, monkeypatch):
    """Sin canal de confirmación (MCP, tests) el permiso lo da el cliente que
    hospeda la tool: no se rompe el camino de siempre."""
    _, ejecutadas = _turno_nube(root, monkeypatch, None)
    assert ejecutadas == ["crear_nube"]


def test_endpoint_confirm_destraba_el_turno_que_espera():
    """El diálogo contesta por /confirm: setea el evento que tiene frenado al turno.
    Sin nada esperando es 404 — no deja un 'sí' guardado para el próximo turno."""
    import threading as th
    from fastapi import HTTPException
    from mem import api

    with pytest.raises(HTTPException) as e:
        api.post_confirm("sesion-fantasma", api.ConfirmIn(ok=True))
    assert e.value.status_code == 404

    evento, estado = th.Event(), {"ok": False}
    api._CONFIRMACIONES["s1"] = (evento, estado)
    try:
        # el job_type puede venir del propio diálogo (Ajustes sin default): tiene que
        # llegar al turno, o crear_nube se llama con el modelo vacío y muere allá
        api.post_confirm("s1", api.ConfirmIn(ok=True, modelo="veo3_1"))
        assert evento.is_set() and estado["ok"] is True and estado["modelo"] == "veo3_1"
    finally:
        api._CONFIRMACIONES.pop("s1", None)


def test_crear_imagen_genera_cataloga_y_se_encuentra(root, monkeypatch):
    """Modo crear: la imagen generada queda como entrada bajo Creaciones/Imagen con
    el prompt y la descripción por visión en el cuerpo — y por eso se encuentra."""
    from mem import crear

    pedidos = []

    def fake_comfy(cfg, rt, prompt, workflow="imagen", referencia="", semilla=None, frames=0, cancelar=None):
        pedidos.append({"workflow": workflow, "frames": frames})
        ext = ".mp4" if workflow.startswith("video") else ".png"
        # nombre único por llamada (como el real, timestamp a la hora/minuto/segundo):
        # dos generaciones nunca deben compartir archivo ni, por lo tanto, entrada.
        rel = f"{crear.ADJUNTOS}/2026-08-03_12000{len(pedidos)}_crear-test{ext}"
        (rt / rel).parent.mkdir(parents=True, exist_ok=True)
        (rt / rel).write_bytes(b"medio")
        return rel, ""

    monkeypatch.setattr(crear, "generar_comfy", fake_comfy)
    monkeypatch.setattr(crear.media, "extraer",
                        lambda ag, rt, adj: ("Una alpaca beige parada frente a un lago andino.", "", True))
    monkeypatch.setattr(crear.triaje, "evaluar", lambda cfg, texto: {
        "titulo": "Alpaca en movimiento" if "(video)" in texto else "Alpaca frente al lago",
        "tags": ["alpaca"], "subjects": []})

    salida = crear.ejecutar({"hamuq": root}, "crear_imagen",
                            {"prompt": "a beige alpaca by an Andean lake"}, proyecto="",
                            sesion="2026-08-04_100000_taller")
    assert salida.startswith("![Alpaca frente al lago](/attach/07_Inbox/_adjuntos/")
    # y el markdown lleva pegada la ficha del medio (el chat la pinta debajo):
    # con qué modelo, en qué servicio (pedido 2026-08-07)
    assert '.png "imagen · ComfyUI (local)")' in salida, salida

    # slug = nombre del ARCHIVO generado, no del título: dos generaciones con
    # títulos parecidos no deben fusionarse en una sola entrada (si no, la
    # segunda queda catalogada pero con el adjunto de la primera — invisible
    # para el Media Manager). El nombre del archivo ya es único (fecha y hora).
    slug = "2026-08-03_120001_crear-test"
    entrada = frontmatter.load(root / f"06_Biblioteca_Conocimiento/Entradas/{slug}.md")
    assert entrada.metadata["subjects"] == ["Proyectos/General", "Creaciones/Imagen"]
    assert entrada.metadata["generado"] is True and entrada.metadata["backend"] == "comfyui"
    assert entrada.metadata["adjunto"].endswith("crear-test.png")
    assert "lago andino" in entrada.content            # la descripción por visión, dentro
    assert "a beige alpaca" in entrada.content         # y el prompt usado
    # la creación recuerda la sesión donde nació: frontmatter (chip) + línea legible
    assert entrada.metadata["sesion"] == "2026-08-04_100000_taller"
    assert "10_Sesiones/2026-08-04_100000_taller.md" in entrada.content
    cats = (root / "08_Categorias/00_CATEGORIAS.md").read_text(encoding="utf-8")
    assert "## Creaciones" in cats and f"{slug}.md" in cats
    assert "lago andino" in memoria.buscar(root, "lago andino")   # se encuentra por contenido
    assert pedidos[-1] == {"workflow": "imagen", "frames": 0}

    # mismo motor para el video: cambia el workflow (y el de referencia usa el que
    # toma la imagen como primer fotograma), y los segundos llegan como fotogramas
    salida = crear.ejecutar({"hamuq": root}, "crear_video",
                            {"prompt": "the alpaca walks, wind and hooves on gravel", "segundos": 8})
    assert pedidos[-1] == {"workflow": "video", "frames": 8 * crear.FPS}
    assert salida.startswith("![Alpaca en movimiento](/attach/07_Inbox/_adjuntos/")
    video = frontmatter.load(root / "06_Biblioteca_Conocimiento/Entradas/2026-08-03_120002_crear-test.md")
    assert video.metadata["subjects"] == ["Proyectos/General", "Creaciones/Video"]
    assert video.metadata["adjunto"].endswith(".mp4")
    # y la entrada de la imagen sigue existiendo aparte, sin fusionarse con la
    # del video pese al título parecido
    assert (root / f"06_Biblioteca_Conocimiento/Entradas/{slug}.md").exists()
    crear.ejecutar({"hamuq": root}, "crear_video",
                   {"prompt": "same but from a photo", "referencia": "/attach/07_Inbox/_adjuntos/x.png"})
    assert pedidos[-1] == {"workflow": "video-imagen", "frames": 0}

    # y el workflow que nombra el MODELO no puede tirar la imagen a la basura: pidió
    # animar una y mandó `workflow: "video"` (t2v) junto con la referencia, así que la
    # imagen se subía a ComfyUI y el video salía del prompt solo (reportado 2026-08-07).
    # Con los workflows a la vista gana el que usa la imagen.
    wfs = root / crear.WORKFLOWS
    wfs.mkdir(parents=True, exist_ok=True)
    (wfs / "video.json").write_text(json.dumps(
        {"1": {"class_type": "SaveVideo", "inputs": {"x": "%prompt%"}}}), encoding="utf-8")
    (wfs / "video-imagen.json").write_text(json.dumps(
        {"0": {"class_type": "LoadImage", "inputs": {"image": "%imagen%"}},
         "1": {"class_type": "SaveVideo", "inputs": {"x": "%prompt%"}}}), encoding="utf-8")
    crear.ejecutar({"hamuq": root}, "crear_video",
                   {"prompt": "same but from a photo", "workflow": "video",
                    "referencia": "/attach/07_Inbox/_adjuntos/x.png"})
    assert pedidos[-1] == {"workflow": "video-imagen", "frames": 0}


def test_mcp_despachar_protocolo_y_tools(root):
    """Servidor MCP: handshake, tools listadas, una escritura real y los errores
    con la forma correcta (isError para la tool, -32601/-32602 para el protocolo)."""
    from mem import mcp

    cfg = {"hamuq": root}
    ini = mcp.despachar({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18"}}, cfg)["result"]
    assert ini["protocolVersion"] == "2025-06-18" and "tools" in ini["capabilities"]
    assert mcp.despachar({"jsonrpc": "2.0", "method": "notifications/initialized"}, cfg) is None

    tools = mcp.despachar({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, cfg)["result"]["tools"]
    assert {"buscar", "capturar", "guardar_entrada", "crear_imagen"} <= {t["name"] for t in tools}
    assert all("inputSchema" in t for t in tools)

    r = mcp.despachar({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "capturar", "arguments": {"contenido": "café pendiente"}}}, cfg)
    assert "capturado: 07_Inbox/" in r["result"]["content"][0]["text"]
    assert memoria.inbox_listar(root)[0]["origen"] == "claude"

    assert mcp.despachar({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}, cfg)["error"]["code"] == -32601
    assert mcp.despachar({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "fantasma"}}, cfg)["error"]["code"] == -32602
    r = mcp.despachar({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                       "params": {"name": "leer_pagina", "arguments": {"path": "../fuera"}}}, cfg)
    assert "fuera de la base" in r["result"]["content"][0]["text"]


def test_mcp_conexiones(root):
    from mem import mcp

    cfg = {"hamuq": root, "app_url": "https://mem.example"}
    memoria.guardar_entrada(root, "Nota IA", "Sobre agentes.", ["IA"])
    memoria.guardar_entrada(root, "Otra de IA", "Relacionada.", ["IA"])
    r = mcp.despachar({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "conexiones", "arguments": {"slug": "nota-ia"}}}, cfg)
    texto = r["result"]["content"][0]["text"]
    cx = json.loads(texto.split("\n\nAbrir en el browser")[0])
    assert cx["entradas"] == [{"slug": "otra-de-ia", "titulo": "Otra de IA"}]
    assert "https://mem.example/#entry/nota-ia" in texto
    r = mcp.despachar({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                       "params": {"name": "conexiones", "arguments": {"slug": "fantasma"}}}, cfg)
    assert r["result"].get("isError") and "FileNotFoundError" in r["result"]["content"][0]["text"]


def test_proyectos_listar_lee_ambito_privado_viejo(root):
    """PROYECTOS.md de antes del cambio traía `ambito: privado`/`personal`/
    `trabajo`; se sigue leyendo como privado=True/False sin migración aparte —
    la próxima escritura ya sale en el formato nuevo."""
    p = root / memoria.PROYECTOS
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(frontmatter.dumps(frontmatter.Post("# Proyectos\n", proyectos=[
        {"nombre": "Terapia", "ambito": "privado", "creado": "2026-01-01"},
        {"nombre": "Museo", "ambito": "personal", "creado": "2026-01-01"},
    ])), encoding="utf-8")
    lista = {p["nombre"]: p for p in memoria.proyectos_listar(root)}
    assert lista["Terapia"]["privado"] is True
    assert lista["Museo"]["privado"] is False


def test_paths_ocultos_tapa_sesiones_de_proyecto_privado(root):
    """grep y leer_pagina trabajan con rutas: una sesión de un proyecto privado
    tiene que quedar tan tapada como las memorias que salgan de ella."""
    memoria.proyecto_guardar(root, "Terapia", True)
    sid = sesiones.crear(root, [], "sesión privada", proyecto="Terapia")
    ruta = f"10_Sesiones/{sid}.md"

    assert ruta in memoria.paths_ocultos(root, "")
    assert ruta in memoria.paths_ocultos(root, None)
    assert ruta not in memoria.paths_ocultos(root, "Terapia")


def test_fijar_proyecto_no_pisa_privado_de_uno_existente(root):
    """El agente puede mudar la sesión a un proyecto que ya existe sin querer
    tocar su privacidad: sin `privado` explícito en el pedido, proyecto_guardar
    no debe volverlo público (pedido 2026-09-05)."""
    memoria.proyecto_guardar(root, "Terapia", True)
    sid = sesiones.crear(root, [], "obra", proyecto="")
    ctx = {"sid": sid, "proyecto": ""}
    chat._ejecutar(root, "fijar_proyecto", {"nombre": "Terapia"}, [], 5, ctx)
    assert ctx["proyecto"] == "Terapia"
    assert memoria.proyecto_por_nombre(root, "Terapia")["privado"] is True


def test_mcp_privado_exige_clave(root):
    """Sin `proyecto` el MCP solo ve lo público; con uno privado hace falta su
    `clave` (o que venga en `claves`, el equivalente a MEM_CLAVES de stdio)."""
    from mem import mcp

    cfg = {"hamuq": root}
    memoria.proyecto_guardar(root, "Diario", True)
    memoria.guardar_entrada(root, "Secreto", "cuerpo", ["Proyectos/Diario"])

    def buscar_memorias(**args):
        r = mcp.despachar({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "buscar_memorias", "arguments": {"texto": "cuerpo", **args}}}, cfg)
        return r["result"]

    assert json.loads(buscar_memorias()["content"][0]["text"].split("\n")[0]) == []
    r = buscar_memorias(proyecto="Diario")   # privado, sin clave
    assert r.get("isError") and "clave" in r["content"][0]["text"]
    r = buscar_memorias(proyecto="Diario", clave="adivinada")
    assert r.get("isError")
    clave = memoria.proyecto_clave(root, "Diario")
    r = buscar_memorias(proyecto="Diario", clave=clave)
    assert json.loads(r["content"][0]["text"].split("\n")[0])[0]["slug"] == "secreto"

    # claves= (lo que main() arma desde MEM_CLAVES): la tool no necesita mandarla
    r = mcp.despachar({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "buscar_memorias",
                                  "arguments": {"texto": "cuerpo", "proyecto": "Diario"}}},
                      cfg, claves={"Diario": clave})
    assert json.loads(r["result"]["content"][0]["text"].split("\n")[0])[0]["slug"] == "secreto"

    assert mcp._claves_env() == {}
    os.environ["MEM_CLAVES"] = f"Diario={clave};Otro=xyz"
    try:
        assert mcp._claves_env() == {"Diario": clave, "Otro": "xyz"}
    finally:
        del os.environ["MEM_CLAVES"]


def test_procesado_de_fondo_coalesce(root, monkeypatch):
    """Una captura que llega MIENTRAS corre una pasada no puede perderse: la
    pasada ya hizo su glob y no la ve, así que el bucle tiene que dar otra
    vuelta. Y dos capturas seguidas no pueden levantar dos hilos a la vez
    (procesar_item mueve los .md de carpeta: dos pasadas se pisarían)."""
    import threading
    import time

    from mem import api

    pasadas = []
    arrancada = threading.Event()
    soltar = threading.Event()

    def falsa(_cfg):
        pasadas.append(1)
        arrancada.set()
        soltar.wait(5)                       # la 1ª pasada queda tomada a propósito
        return {"procesadas": 1, "errores": 0}

    monkeypatch.setattr(api.procesar, "procesar_inbox", falsa)
    monkeypatch.setitem(api.cfg, "hamuq", root)
    api._estado.update(corriendo=False, hechas=0, errores=0, otra=False)

    assert api._arrancar_procesado() is True     # arranca el hilo
    arrancada.wait(5)
    assert api._arrancar_procesado() is False    # ya hay uno: solo marca "otra"
    assert api._estado["otra"] is True
    soltar.set()

    for _ in range(200):                         # espera a que el bucle termine
        if not api._estado["corriendo"]:
            break
        time.sleep(0.02)
    assert api._estado["corriendo"] is False
    assert len(pasadas) == 2, "la captura de la 2ª llamada tiene que forzar otra vuelta"
    assert api._estado["hechas"] == 2            # el contador que mira el cliente sube


def test_privado_es_del_proyecto_no_de_la_memoria(root):
    """Togglear un proyecto cambia la visibilidad de todo lo suyo al instante,
    sin campo propio en la memoria que migrar (pedido 2026-09-05)."""
    memoria.proyecto_guardar(root, "Terapia", True)
    memoria.proyecto_guardar(root, "Museo", False)

    memoria.guardar_entrada(root, "Sesión del martes", "lo hablado", ["Proyectos/Terapia"])
    memoria.guardar_entrada(root, "Maqueta del hall", "avances", ["Proyectos/Museo"])

    def slugs(p=None):
        return {m["slug"] for m in memoria.buscar_memorias(root, proyecto=p)}

    assert "sesion-del-martes" not in slugs()          # "Todo" es solo lo público
    assert "sesion-del-martes" in slugs("Terapia")     # desde el suyo, sí
    assert "maqueta-del-hall" in slugs()                # público: se ve desde Todo

    # Terapia deja de ser privado: se destapa al instante, sin tocar la memoria
    memoria.proyecto_guardar(root, "Terapia", False)
    assert "sesion-del-martes" in slugs()

    # y volver a marcarlo privado la tapa de nuevo, también al instante
    memoria.proyecto_guardar(root, "Terapia", True)
    assert "sesion-del-martes" not in slugs()


def test_un_proyecto_por_memoria(root):
    """Una memoria vive en un solo proyecto (pedido 2026-09-05): con la
    privacidad del lado del proyecto, dos proyectos en una misma memoria no
    tienen respuesta que tenga sentido — se queda con el primero."""
    assert memoria.un_proyecto(["Proyectos/A", "Salud", "Proyectos/A/Hijo"]) \
        == ["Proyectos/A", "Salud", "Proyectos/A/Hijo"]
    assert memoria.un_proyecto(["Proyectos/A", "Salud", "Proyectos/B"]) == ["Proyectos/A", "Salud"]

    memoria.proyecto_guardar(root, "A", False)
    memoria.proyecto_guardar(root, "B", False)
    memoria.guardar_entrada(root, "Doble", "cuerpo", ["Proyectos/A", "Proyectos/B"])
    assert memoria.leer_entrada(root, "doble")["subjects"] == ["Proyectos/A"]


def test_migrar_un_proyecto_recorta_las_viejas(root):
    """Las entradas de antes de la migración pueden tener dos `Proyectos/<n>`
    en subjects: la migración one-shot las recorta al primero y no vuelve a
    tocarlas."""
    memoria.proyecto_guardar(root, "A", False)
    memoria.proyecto_guardar(root, "B", False)
    p = root / "06_Biblioteca_Conocimiento/Entradas/vieja.md"
    p.write_text(frontmatter.dumps(frontmatter.Post(
        "# Vieja\n", titulo="Vieja", subjects=["Proyectos/A", "Proyectos/B"])), encoding="utf-8")

    assert memoria.migrar_un_proyecto(root) == 1
    assert frontmatter.load(p).metadata["subjects"] == ["Proyectos/A"]
    assert memoria.migrar_un_proyecto(root) == 0, "idempotente: la 2ª corrida no escribe"


def test_migrar_general_mete_lo_suelto_en_un_proyecto(root):
    """Todo vive en un proyecto (pedido 2026-09-05): lo que quedó suelto —una
    entrada, una sesión, un item del inbox— pasa a General, que se crea si no
    estaba. Lo que YA tiene proyecto no se toca."""
    memoria.proyecto_guardar(root, "Obra", False)
    suelta = root / "06_Biblioteca_Conocimiento/Entradas/suelta.md"
    suelta.parent.mkdir(parents=True, exist_ok=True)
    suelta.write_text(frontmatter.dumps(frontmatter.Post(
        "# Suelta\n", titulo="Suelta", subjects=["Tecnologia/IA"])), encoding="utf-8")
    dela_obra = root / "06_Biblioteca_Conocimiento/Entradas/de-la-obra.md"
    dela_obra.write_text(frontmatter.dumps(frontmatter.Post(
        "# Obra\n", titulo="Obra", subjects=["Proyectos/Obra"])), encoding="utf-8")
    ses = root / "10_Sesiones/2026-09-05_000000.md"
    ses.parent.mkdir(parents=True, exist_ok=True)
    ses.write_text(frontmatter.dumps(frontmatter.Post("charla\n", id="2026-09-05_000000")), encoding="utf-8")

    assert memoria.migrar_general(root) == 3   # las dos entradas sueltas (una es del fixture) y la sesión
    assert frontmatter.load(suelta).metadata["subjects"] == ["Proyectos/General", "Tecnologia/IA"]
    assert frontmatter.load(dela_obra).metadata["subjects"] == ["Proyectos/Obra"], "lo que ya tenía no se toca"
    assert frontmatter.load(ses).metadata["proyecto"] == "General"
    assert any(p["nombre"] == "General" and not p["privado"] for p in memoria.proyectos_listar(root))
    assert memoria.migrar_general(root) == 0, "idempotente: la 2ª corrida no escribe"


def test_lo_que_se_guarda_sin_proyecto_cae_en_general(root):
    """La otra mitad del invariante: una captura o una entrada nueva sin
    proyecto (MCP, CLI) tampoco puede quedar suelta — si no, no la lista
    ninguna pantalla, porque el selector ya no tiene un "Todo"."""
    memoria.guardar_entrada(root, "Sin dueño", "cuerpo", ["Tecnologia/IA"])
    assert memoria.leer_entrada(root, "sin-dueno")["subjects"] == ["Proyectos/General", "Tecnologia/IA"]
    ruta = memoria.capturar(root, "una nota suelta")
    assert frontmatter.load(root / ruta).metadata["subjects"] == ["Proyectos/General"]


# --------------------------------------------------------------------------
# Patrón "LLM Wiki" (Karpathy) adaptado: que el conocimiento componga. Tres
# piezas — avisos semánticos (el LLM propone, Diego confirma), contraste en la
# ingesta y páginas de síntesis por tema.
# --------------------------------------------------------------------------

class FakeLLMTurnos:
    """Una respuesta distinta por llamada; lleva la cuenta y guarda lo que vio."""
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = 0
        self.visto = []

    def completar(self, system, msgs, tools=None):
        self.visto.append("\n".join(m["content"] for m in msgs))
        r = self.respuestas[min(self.llamadas, len(self.respuestas) - 1)]
        self.llamadas += 1
        if isinstance(r, Exception):
            raise r
        return {"texto": r, "tool_calls": [], "tokens_in": 10, "tokens_out": 10}


def test_avisos_dedupean_y_el_descarte_es_definitivo(root):
    """El aviso es la unidad que comparten la ingesta y el lint semántico: el
    mismo hallazgo desde los dos lados es UNO, y lo que Diego descarta no revive
    en la siguiente corrida del pase caro."""
    assert lint.aviso_agregar(root, "contradiccion", ["a", "b"], "la fecha cambió", "ingesta")
    assert not lint.aviso_agregar(root, "contradiccion", ["b", "a"], "otra vez", "lint"), \
        "el mismo par en el otro sentido es el mismo aviso"
    assert not lint.aviso_agregar(root, "contradiccion", ["a", "c"], "", "lint"), "sin detalle no hay aviso"
    assert not lint.aviso_agregar(root, "inventado", ["a"], "x", "lint"), "tipo fuera de la whitelist"

    clave = lint.clave_aviso("contradiccion", ["a", "b"])
    assert any(clave in p and "la fecha cambió" in p for p in lint.correr(root))

    lint.ignorar(root, "avisos", clave)
    lint.aviso_quitar(root, clave)
    assert not any(clave in p for p in lint.correr(root))
    assert not lint.aviso_agregar(root, "contradiccion", ["a", "b"], "la fecha cambió", "lint"), \
        "descartado por Diego = no vuelve, ni siquiera desde otro pase"


def test_anotar_registro_deja_linea_fechada_sin_versionar(root):
    """Aceptar un aviso escribe en el registro histórico (append-only) y nada
    más: no toca el resumen, así que no versiona (igual que enlazar_entradas)."""
    memoria.guardar_entrada(root, "Precio del visor", "Costaba 500 dólares.", ["Tecnologia/VR"])
    memoria.guardar_entrada(root, "Visor más barato", "Ahora cuesta 300.", ["Tecnologia/VR"])
    p = root / memoria.ENTRADAS / "precio-del-visor.md"
    version_antes = frontmatter.load(p).metadata.get("version")

    memoria.anotar_registro(root, "precio-del-visor", "revisar contra [[visor-mas-barato]]: bajó a 300")
    post = frontmatter.load(p)
    assert "[[visor-mas-barato]]" in post.content and "(aviso)" in post.content
    assert "entrada creada" in post.content, "el registro es append-only: lo viejo sigue"
    assert "Costaba 500 dólares." in post.content, "el resumen no se toca"
    assert post.metadata.get("version") == version_antes
    assert not (root / memoria.VERSIONES / str(post.metadata["id"])).exists()


def test_contraste_en_ingesta_deja_aviso_para_confirmar(root, monkeypatch):
    """Lo que a MeM le faltaba del patrón wiki: al entrar material nuevo, se
    contrasta con lo que ya se sabía. No edita la memoria vieja — deja un aviso."""
    from mem import indice
    memoria.guardar_entrada(root, "Precio del visor", "Costaba 500 dólares.", ["Tecnologia/VR"])
    fake = FakeLLMTurnos(
        json.dumps({"titulo": "Visor más barato", "sintesis": "Ahora cuesta 300 dólares.",
                    "tipo": "nota", "tags": [], "subjects": ["Tecnologia/VR"]}),
        json.dumps({"avisos": [{"slug": "precio-del-visor", "tipo": "actualiza",
                                "detalle": "el precio pasó de 500 a 300"},
                               {"slug": "no-existe", "tipo": "contradice", "detalle": "inventado"}]}))
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: fake)
    monkeypatch.setattr(indice, "vecinas", lambda rt, slug, k=5: [("precio-del-visor", 0.88)])

    r = procesar.procesar_item({"hamuq": root}, root / memoria.capturar(root, "el visor bajó de precio"))
    assert r["estado"] == "procesada"
    avisos = lint.avisos_cargar(root)
    assert len(avisos) == 1, "el slug inventado por el modelo se descarta"
    assert avisos[0]["tipo"] == "obsoleta" and avisos[0]["origen"] == "ingesta"
    assert avisos[0]["slugs"][0] == "precio-del-visor", "slugs[0] = la memoria afectada"
    # la memoria vieja sigue intacta: el aviso propone, no escribe
    assert "300" not in (root / memoria.ENTRADAS / "precio-del-visor.md").read_text(encoding="utf-8")


def test_contraste_que_revienta_no_arruina_el_procesado(root, monkeypatch):
    """El item ya terminó su viaje cuando corre el contraste: si el modelo falla
    ahí, la captura igual queda procesada y la entrada guardada."""
    from mem import indice
    memoria.guardar_entrada(root, "Precio del visor", "Costaba 500.", ["Tecnologia/VR"])
    fake = FakeLLMTurnos(
        json.dumps({"titulo": "Visor barato", "sintesis": "Cuesta 300.", "tipo": "nota",
                    "tags": [], "subjects": ["Tecnologia/VR"]}),
        RuntimeError("el modelo se cayó"))
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: fake)
    monkeypatch.setattr(indice, "vecinas", lambda rt, slug, k=5: [("precio-del-visor", 0.88)])

    p = root / memoria.capturar(root, "bajó de precio")
    assert procesar.procesar_item({"hamuq": root}, p)["estado"] == "procesada"
    assert not p.exists() and (root / memoria.ENTRADAS / "visor-barato.md").exists()
    assert lint.avisos_cargar(root) == []


def test_contraste_sin_vecinas_no_gasta_una_llamada(root, monkeypatch):
    """Base chica o tema nuevo (el caso común): sin vecindario real no se paga."""
    from mem import indice
    fake = FakeLLMTurnos(json.dumps({"titulo": "Tema nuevo", "sintesis": "Algo.", "tipo": "nota",
                                     "tags": [], "subjects": ["Tecnologia/IA"]}))
    monkeypatch.setattr(procesar.llm_mod, "crear", lambda cfg: fake)
    monkeypatch.setattr(indice, "vecinas", lambda rt, slug, k=5: [])
    procesar.procesar_item({"hamuq": root}, root / memoria.capturar(root, "algo nuevo"))
    assert fake.llamadas == 1, "solo la del catalogador"


def test_lint_semantico_valida_y_es_idempotente(root, monkeypatch):
    """El pase caro es bajo demanda: correrlo dos veces no duplica, los slugs
    inventados se caen y lo descartado no vuelve."""
    memoria.guardar_entrada(root, "Precio del visor", "Costaba 500.", ["Tecnologia/VR"])
    memoria.guardar_entrada(root, "Visor barato", "Cuesta 300.", ["Tecnologia/VR"])
    fake = FakeLLMTurnos(json.dumps({"avisos": [
        {"tipo": "contradiccion", "slugs": ["precio-del-visor", "visor-barato"], "detalle": "500 vs 300"},
        {"tipo": "obsoleta", "slugs": ["fantasma"], "detalle": "slug inventado"},
        {"tipo": "hueco", "slugs": [], "detalle": "falta el modelo exacto del visor"}]}))
    monkeypatch.setattr(lint.llm_mod, "crear", lambda cfg: fake)

    nuevos = lint.semantico({"hamuq": root})
    assert [n["tipo"] for n in nuevos] == ["contradiccion", "hueco"], "sin memoria afectada no hay aviso"
    assert lint.semantico({"hamuq": root}) == [], "segunda corrida: nada nuevo"
    assert len(lint.avisos_cargar(root)) == 2

    clave = lint.clave_aviso("hueco", [])
    lint.ignorar(root, "avisos", clave)
    lint.aviso_quitar(root, clave)
    assert lint.semantico({"hamuq": root}) == [], "lo descartado no revive"


def test_sintesis_es_una_pagina_viva_del_tema(root, monkeypatch):
    """La página de síntesis es una entrada normal (versionado, registro, índices
    gratis), se regenera sobre sí misma y el procesador la mantiene al día."""
    from mem import sintesis
    memoria.guardar_entrada(root, "Visor A", "Pesa 500 gramos.", ["Tecnologia/VR"])
    memoria.guardar_entrada(root, "Visor B", "Pesa 400 gramos.", ["Tecnologia/VR"])
    monkeypatch.setattr(sintesis.llm_mod, "crear",
                        lambda cfg: FakeLLMTurnos("Los visores pesan entre 400 y 500 gramos."))

    r = sintesis.sintetizar({"hamuq": root}, "Tecnologia/VR")
    assert r["slug"] == "sintesis-tecnologia-vr" and r["fuentes"] == 2
    p = root / memoria.ENTRADAS / "sintesis-tecnologia-vr.md"
    post = frontmatter.load(p)
    assert post.metadata["sintesis_de"] == "Tecnologia/VR"
    assert set(post.metadata["fuentes"]) == {"visor-a", "visor-b"}
    assert "(Entradas/visor-a.md)" in post.content, "los links los arma el código"
    assert "[[" not in post.content, "ningún automatismo escribe wikilinks (§7.14)"
    assert sintesis.desactualizadas(root) == [], "sin material nuevo no hay nada que refrescar"

    # una memoria nueva bajo el tema la deja atrás; refrescar regenera SOBRE la
    # misma página y la anterior queda en _versiones/
    memoria.guardar_entrada(root, "Visor C", "Pesa 300 gramos.", ["Tecnologia/VR"])
    assert sintesis.desactualizadas(root) == ["Tecnologia/VR"]
    assert sintesis.refrescar({"hamuq": root})["refrescadas"] == ["Tecnologia/VR"]
    post = frontmatter.load(p)
    assert post.metadata["version"] == 2 and "visor-c" in post.metadata["fuentes"]
    assert (root / memoria.VERSIONES / str(post.metadata["id"]) / "v001.md").exists()

    # una síntesis no se alimenta de otra, ni desde el subject padre: anti-loop
    assert [f["slug"] for f in sintesis.fuentes_de(root, "Tecnologia")] == ["visor-a", "visor-b", "visor-c"]
    # y refrescar NUNCA inventa una página para un tema que Diego no eligió
    memoria.guardar_entrada(root, "Nota de obra", "Llegó el cemento.", ["Casa/Obra"])
    assert sintesis.refrescar({"hamuq": root})["refrescadas"] == []
    assert not (root / memoria.ENTRADAS / "sintesis-casa-obra.md").exists()


def test_lint_no_confunde_una_sintesis_con_un_duplicado(root, monkeypatch):
    """Una síntesis SE PARECE a sus fuentes: es su trabajo. Sin la exclusión, el
    chequeo de duplicados avisaría por cada tema sintetizado."""
    from mem import indice, sintesis
    memoria.guardar_entrada(root, "Visor A", "Pesa 500 gramos y cuesta caro.", ["Tecnologia/VR"])
    monkeypatch.setattr(sintesis.llm_mod, "crear",
                        lambda cfg: FakeLLMTurnos("Pesa 500 gramos y cuesta caro."))
    sintesis.sintetizar({"hamuq": root}, "Tecnologia/VR")
    monkeypatch.setattr(indice, "duplicados",
                        lambda rt, umbral=0.92: [("sintesis-tecnologia-vr", "visor-a", 0.97)])
    assert not [x for x in lint.correr(root) if x.startswith("posible duplicado")]
