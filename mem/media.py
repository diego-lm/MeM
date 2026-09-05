"""Adjuntos → texto. Punto único: `extraer(cfg, root, adjunto)`.

Todo lo que entra a la memoria termina siendo texto: lo escrito, lo que dice una
página, lo que se VE en una imagen o un video y lo que se OYE en un audio. Sin
esto un adjunto es un nombre de archivo — no se puede categorizar, ni indexar, ni
encontrar después. Lo que se saca acá va a dos lados: al prompt del procesador
(para clasificar) y, literal, al cuerpo de la entrada (para buscar).
"""
import importlib
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from . import llm as llm_mod

IMAGENES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")
TEXTOS = (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".html", ".htm", ".srt", ".vtt")
VIDEO = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg")
AUDIO = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".flac", ".weba", ".wma")
DOCS = (".pdf",)

FOTOGRAMAS = 4          # piso de tomas repartidas por la duración, pase lo que pase
MAX_FOTOGRAMAS = 60     # techo por video: 60 imágenes ya son ~15 viajes al modelo de visión
GRUPO = 4               # fotogramas por pedido: van juntos para que el modelo vea qué cambia
IMAGENES_PDF = 8        # tope de imágenes a describir en un PDF sin capa de texto
MAX_CHARS = 12_000      # lo que puede entrar a un prompt de un tirón; arriba de esto se condensa
WHISPER = "small"       # ponytail: modelo faster-whisper por defecto; "medium"/"large-v3" si falla el español

# "Mirá esto": el que habla está SEÑALANDO algo que solo se ve en la imagen. Ahí
# hay que ir a buscar un fotograma o la memoria se queda con "mostró algo".
# ponytail: solo verbos imperativos de mirar. Sumar "esto/this/here" parecía obvio
# y es justo lo que no hay que hacer: están en cada frase hablada, saturarían
# MAX_FOTOGRAMAS en los primeros minutos y tirarían abajo los cambios de escena.
RX_CUE = re.compile(r"\b(mir[aáe]n?|fij[aá](te|ense)|vean|observen|look at|watch this|"
                    r"as you can see|ac[aá] se ve|aqu[ií] (se ve|vemos)|check this out)\b", re.I)

PROMPT_IMAGEN = (
    "Describe en español, con TODO el detalle que puedas, el contenido de esta imagen: "
    "qué se ve (objetos, personas, lugar, marcas, colores, acciones), TODO el texto legible "
    "transcrito literalmente, y de qué trata. Esta descripción REEMPLAZA a la imagen dentro de "
    "una memoria personal de solo texto: lo que no escribas se pierde para siempre. "
    "No inventes lo que no se ve.")

PROMPT_CONDENSAR = (
    "Resumí en español este tramo de la línea de tiempo de un video, sin perder nada concreto: "
    "nombres, cifras, lugares, decisiones, conclusiones, y QUÉ SE MOSTRÓ en pantalla. "
    "Conservá las marcas de tiempo [mm:ss] de lo que resumas. Es la única versión que va a leer "
    "quien decida de qué trata el video: no opines, no agregues nada que no esté, no lo cortes "
    "a la mitad. Devolvé solo el resumen.")


def _opcional(nombre: str):
    """Dependencia que puede no estar instalada. Se prefiere fallar con un mensaje
    que diga qué instalar antes que exigirla a todo el mundo para un caso que
    quizá nunca aparezca."""
    try:
        return importlib.import_module(nombre)
    except ImportError:
        return None


def _del_endpoint(cfg: dict, necesita: str) -> dict | None:
    """cfg apuntando a un modelo del MISMO endpoint que acepte `necesita` de entrada.

    El agente de procesar puede no tener visión —gpt-oss-20b devolvía BadRequest y
    el adjunto se perdía en silencio— pero el catálogo del proveedor ya dice quién
    sí. Elegirlo solo, en vez de pedirle a Diego que cambie el agente a mano.
    """
    if cfg.get("proveedor") == "claude_code":
        return None                      # el CLI (-p) es solo texto en este wrapper
    if necesita in llm_mod.modalidades(cfg.get("modelo", ""))[0]:
        return cfg
    try:
        capaces = [m["id"] for m in llm_mod.modelos(cfg) if necesita in m["in"] and m["out"]]
    except Exception:
        capaces = []
    return {**cfg, "modelo": capaces[0]} if capaces else None


def _con_modelo(cfg: dict, necesita: str) -> dict | None:
    """Quién puede leer esto: el agente que toca, o cualquier otro configurado.

    Si el agente asignado no puede ver ni oír, se le pide prestado el modelo a otro
    agente. Hace falta de verdad: el chat lo atiende claude_code, que es solo texto,
    así que compartir una foto desde WhatsApp terminaba en "ningún modelo con
    visión" teniendo un VLM cargado en la máquina (2026-08-06).
    """
    from . import agentes
    if c := _del_endpoint(cfg, necesita):
        return c
    for a in agentes.cargar(cfg).get("agentes", []):
        otro = {**cfg, **{k: a.get(k, "") for k in agentes.CAMPOS_PROV}}
        if c := _del_endpoint(otro, necesita):
            return c
    return None


def _endpoint(cfg: dict) -> str:
    return cfg.get("base_url") or cfg.get("proveedor") or "el proveedor"


# ---------------------------------------------------------------- imagen

def describir_imagen(cfg: dict, p: Path | list[Path], prompt: str = PROMPT_IMAGEN) -> tuple[str, str]:
    """`p` puede ser una lista: los fotogramas de un video van juntos en un pedido."""
    c = _con_modelo(cfg, "i")
    if not c:
        return "", f"ningún modelo con visión en {_endpoint(cfg)}"
    try:
        return llm_mod.describir_imagen(c, p, prompt).strip(), ""
    except Exception as e:
        return "", f"{c['modelo']} no pudo leer la imagen ({type(e).__name__})"


# ---------------------------------------------------------------- audio

_DEVICE = "auto"        # baja a "cpu" en cuanto se comprueba que CUDA está a medias


def _dll_cuda() -> None:
    """Las DLL de cuBLAS/cuDNN que ctranslate2 busca por PATH y pip deja adentro de
    site-packages/nvidia/*/bin, donde nadie las mira. Con el extra [audio-gpu]
    instalado esto es todo lo que separa a whisper de la GPU; sin él no hace nada
    y se sigue por CPU. Medido con voz corta: 6.2s en CPU contra 2.1s en GPU con
    la carga del modelo incluida — en una charla larga la diferencia es la que
    hay entre esperar una hora y esperar unos minutos."""
    import os
    for paquete in ("nvidia.cublas", "nvidia.cudnn"):
        mod = _opcional(paquete)
        # son paquetes de espacio de nombres: __file__ es None y hay que ir por
        # __path__ (Path(None) reventaba adentro de _whisper y se leía como "no
        # pudo transcribir", que es lo último que uno mira cuando falla la GPU)
        for raiz in getattr(mod, "__path__", []) or []:
            if (bin_ := Path(raiz) / "bin").is_dir():
                # PATH y no solo add_dll_directory: ctranslate2 las carga por
                # nombre desde su propio .pyd con un LoadLibrary común, que no
                # mira los directorios agregados por Python. Con add_dll_directory
                # solo, sigue diciendo "cublas64_12.dll is not found" teniéndola
                # ahí al lado (comprobado 2026-09-05).
                if str(bin_) not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"
                try:
                    os.add_dll_directory(str(bin_))
                except (OSError, AttributeError):    # ya agregado, o no es Windows
                    pass


@lru_cache(maxsize=2)   # 2: el "auto" que falló y el "cpu" que lo reemplaza
def _whisper(modelo: str, device: str):
    fw = _opcional("faster_whisper")
    if not fw:
        return None
    _dll_cuda()
    return fw.WhisperModel(modelo, device=device, compute_type="int8")


def _segmentos(cfg: dict, p: Path) -> tuple[list[tuple[float, float, str]], str]:
    """Audio → [(inicio, fin, texto)]. Primero faster-whisper si está instalado
    (local, offline); si no, el endpoint estándar /v1/audio/transcriptions.

    Los tiempos son la mitad de lo que hace entendible un video: sin ellos "mirá
    esto" y el fotograma donde está el pájaro nunca se encuentran. El endpoint de
    respaldo no los da, así que devuelve un único segmento en 0 — el que llama ya
    tiene que aguantar que un video no tenga marcas de tiempo.

    ponytail: LM Studio (probado 2026-08) no acepta audio ni por /v1/audio/... ni
    como content part, así que sin una de las dos vías el audio queda pendiente y
    el mensaje dice exactamente qué falta. Cuando LM Studio soporte audio, basta
    con que `llm.modalidades` marque el modelo y esto lo toma solo.
    """
    global _DEVICE
    if _opcional("faster_whisper"):
        # `device="auto"` elige CUDA en cuanto ve la GPU, pero ctranslate2 pide
        # cuBLAS/cuDNN por su cuenta (no vienen en el paquete) y si faltan revienta
        # acá, TRANSCRIBIENDO, no al construir el modelo: por eso el reintento vive
        # en esta función y no en _whisper.
        # En CPU int8 "small" va a ~1x tiempo real (medido: 7.9s para 7s de voz):
        # una charla de una hora tarda una hora. Para eso está el extra [audio-gpu]
        # (nvidia-cublas-cu12 + nvidia-cudnn-cu12), que `_dll_cuda` engancha solo
        # (verificado en esta máquina el 2026-09-05: transcribe en CUDA).
        # vad_filter saltea los silencios: gratis, y en un video largo es mucho.
        # Y va envuelto porque un ASR roto no puede tumbar la catalogación entera:
        # el contrato de este módulo es devolver (contenido, motivo), nunca explotar.
        falla = ""
        for device in dict.fromkeys([_DEVICE, "cpu"]):
            try:
                segs, _ = _whisper(WHISPER, device).transcribe(str(p), vad_filter=True)
                fuera = [(s.start, s.end, s.text.strip()) for s in segs if s.text.strip()]
                _DEVICE = device
                return fuera, ""
            except Exception as e:
                falla = f"faster-whisper no pudo transcribir en {device} ({type(e).__name__}: {e})"
        return [], falla[:300]
    base = cfg.get("base_url")
    if base:
        try:
            import os

            from openai import OpenAI
            asr = next((m["id"] for m in llm_mod.modelos(cfg) if m["in"] == "a"), "whisper-1")
            cliente = OpenAI(base_url=base, api_key=os.environ.get(cfg.get("api_key_env", ""), "") or "lm-studio")
            with p.open("rb") as f:
                texto = (cliente.audio.transcriptions.create(model=asr, file=f).text or "").strip()
            return ([(0.0, 0.0, texto)] if texto else []), ""
        except Exception:
            pass
    return [], ("sin transcripción — instalar faster-whisper (pip install faster-whisper) "
                f"o apuntar el proveedor a un servidor con /v1/audio/transcriptions ({_endpoint(cfg)} no lo tiene)")


def transcribir(cfg: dict, p: Path) -> tuple[str, str]:
    """Audio → texto corrido. Lo que usan las notas de voz, que no tienen adónde
    poner una marca de tiempo; el video usa `_segmentos` directo."""
    segs, falta = _segmentos(cfg, p)
    return " ".join(t for _, _, t in segs).strip(), falta


# ---------------------------------------------------------------- video

def _ffmpeg(*args) -> bool:
    return subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                          capture_output=True).returncode == 0


def _duracion(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


RX_VTT = re.compile(r"^(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s*-->\s*(?:(\d+):)?(\d+):(\d+)[.,](\d+)")
RX_TAG = re.compile(r"<[^>]+>")


def _parsear_vtt(texto: str) -> list[tuple[float, float, str]]:
    """WebVTT → [(inicio, fin, texto)]. Los subtítulos automáticos de YouTube
    vienen con tags `<c>` de karaoke y en modo roll-up: cada cue repite la línea
    anterior más una palabra. Sin quitar las repetidas, una charla de una hora
    sale tres veces más larga y con todo dicho de a pedazos.

    ponytail: se recorre por líneas y no con un regex de cue entero — el que
    abarcaba el cuerpo se comía la línea en blanco que separa un cue del
    siguiente y devolvía el archivo entero como un solo subtítulo."""
    out: list[tuple[float, float, str]] = []
    tiempos, cuerpo = None, []

    def cerrar():
        linea = " ".join(RX_TAG.sub("", " ".join(cuerpo)).split())
        if tiempos and linea and (not out or linea != out[-1][2]):
            out.append((*tiempos, linea))

    for cruda in texto.splitlines():
        if m := RX_VTT.match(cruda.strip()):
            cerrar()
            h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()
            tiempos = (int(h1 or 0) * 3600 + int(m1) * 60 + int(s1) + int(ms1.ljust(3, "0")[:3]) / 1000,
                       int(h2 or 0) * 3600 + int(m2) * 60 + int(s2) + int(ms2.ljust(3, "0")[:3]) / 1000)
            cuerpo = []
        elif tiempos is not None and cruda.strip():
            cuerpo.append(cruda.strip())
    cerrar()
    return out


def _subtitulos(p: Path) -> list[tuple[float, float, str]]:
    """La pista de subtítulos que ya trae el archivo, si la trae. Es lo que baja
    yt-dlp de YouTube (los pega adentro del mp4): sale en un segundo y con los
    tiempos puestos, contra la hora que tarda whisper. Vacío = hay que transcribir."""
    for mapa in ("0:s:m:language:spa", "0:s:0"):
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-map", mapa, "-f", "webvtt", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and (segs := _parsear_vtt(r.stdout or "")):
            return segs
    return []


def _escenas(p: Path, umbral: float = 0.4) -> list[float]:
    """Segundos donde la imagen CAMBIA. Es lo que convierte 4 tomas al azar en
    tomas de cada cosa que se mostró.

    ponytail: decodifica el video entero (~1-3 min por hora a 360p) y corre en el
    hilo de fondo, así que no molesta a nadie. Si algún día molesta, el escape es
    `-skip_frame nokey` (más rápido, más ruidoso)."""
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(p), "-an", "-sn",
                        "-vf", f"select='gt(scene,{umbral})',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", r.stderr or "")]


def fotogramas(p: Path, destino: Path, n: int = FOTOGRAMAS,
               extra: list[float] | tuple[float, ...] = ()) -> list[tuple[float, Path]]:
    """n tomas repartidas por la duración, al centro de cada tramo (el frame 0 de
    un video suele ser negro), más los segundos de `extra` —cambios de escena y
    momentos donde alguien dice "mirá esto"—. Devuelve [(segundo, jpg)].

    El piso repartido se queda pase lo que pase —un video de cámara fija no tiene
    cambios de escena y aun así hay que mirarlo— y no se descarta a sí mismo: en
    un clip de 3 segundos las cuatro tomas caen a menos de un segundo una de otra
    y siguen siendo cuatro tomas distintas. El filtro de 5s es solo para lo que
    llega por `extra`, que viene de dos fuentes que se pisan entre ellas."""
    dur = _duracion(p)
    if dur <= 0:
        return []
    momentos = [dur * (i + 0.5) / n for i in range(n)]
    for t in sorted(x for x in extra if 0 < x < dur):
        if all(abs(t - y) >= 5 for y in momentos):
            momentos.append(t)
    momentos.sort()
    if len(momentos) > MAX_FOTOGRAMAS:
        # repartido, no los primeros 60: cortar por la cabeza deja el final del
        # video sin mirar, que es justo donde suele estar la conclusión
        paso = len(momentos) / MAX_FOTOGRAMAS
        momentos = [momentos[int(i * paso)] for i in range(MAX_FOTOGRAMAS)]
    out = []
    for i, t in enumerate(momentos):
        f = destino / f"f{i}.jpg"
        # -ss antes de -i = seek rápido por keyframe; suficiente para un resumen
        if _ffmpeg("-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1",
                   "-vf", "scale='min(768,iw)':-2", str(f)) and f.is_file():
            out.append((t, f))
    return out


def _mmss(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def _log(cfg: dict, msg: str) -> None:
    """Un video largo tarda minutos u horas y el procesador no tiene barra de
    progreso: sin estas líneas en el log, MeM parece colgado."""
    if raiz := cfg.get("hamuq"):
        from . import memoria
        try:
            memoria.log_evento(Path(raiz), "media", msg)
        except Exception:
            pass


def describir_video(cfg: dict, p: Path) -> tuple[str, str]:
    """Video → una LÍNEA DE TIEMPO de lo que se oye y lo que se ve, mezcladas.

    Que estén mezcladas y con la hora puesta es el punto: "mirá, esto es lo que
    te quería mostrar" no significa nada suelto, y al lado del fotograma del
    segundo siguiente significa que mostró un pájaro. Las dos vías son
    independientes: si solo una responde, se guarda esa y la otra se reporta como
    faltante — medio contenido es mejor que ninguno.

    El habla se agrupa por minuto: whisper devuelve ~1000 segmentos por hora y
    una marca de tiempo por frase es más ruido que dato.
    """
    if not shutil.which("ffmpeg"):
        return "", "video sin procesar — falta ffmpeg en el PATH"
    linea: list[tuple[float, str]] = []
    faltan = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if segs := _subtitulos(p):
            _log(cfg, f"{p.name}: {len(segs)} subtítulos ya venían en el archivo")
        else:
            wav = tmp / "audio.wav"
            if _ffmpeg("-i", str(p), "-vn", "-ac", "1", "-ar", "16000", str(wav)) and wav.is_file():
                _log(cfg, f"{p.name}: transcribiendo audio ({_duracion(p) / 60:.0f} min)")
                segs, falta = _segmentos(cfg, wav)
                if falta:
                    faltan.append(falta)
            else:
                segs = []
                faltan.append("no se pudo extraer la pista de audio")
        por_minuto: dict[int, list[str]] = {}
        for ini, _fin, texto in segs:
            por_minuto.setdefault(int(ini // 60), []).append(texto)
        linea += [(m * 60, f"[{_mmss(m * 60)}] 🗣 {' '.join(dichos)}") for m, dichos in por_minuto.items()]

        cues = [ini for ini, _f, texto in segs if RX_CUE.search(texto)]
        tomas = fotogramas(p, tmp, extra=_escenas(p) + cues)
        if not tomas:
            faltan.append("no se pudieron extraer fotogramas")
        grupos = [tomas[i:i + GRUPO] for i in range(0, len(tomas), GRUPO)]
        for i, grupo in enumerate(grupos, 1):
            _log(cfg, f"{p.name}: visión {i}/{len(grupos)}")
            etiquetas = ", ".join(f"[{_mmss(t)}]" for t, _ in grupo)
            # ponytail: no se parsea la respuesta — el bloque entero entra a la
            # línea de tiempo en el segundo del primer fotograma del grupo. Pedirle
            # al modelo un formato exacto y creerle es cómo se pierde una toma.
            texto, falta = describir_imagen(
                cfg, [f for _, f in grupo],
                f"Estos son {len(grupo)} fotogramas de un video, en orden, tomados en {etiquetas}. "
                f"Describí CADA UNO en un párrafo aparte que empiece por su marca de tiempo entre "
                f"corchetes, y decí qué cambia de uno al siguiente. {PROMPT_IMAGEN}")
            if texto:
                linea.append((grupo[0][0], f"[{_mmss(grupo[0][0])}] 👁 {texto}"))
            elif falta:
                if falta not in faltan:
                    faltan.append(falta)
                break                     # sin visión, los demás fotogramas fallan igual
    if not linea:
        return "", f"video ({p.suffix}): {'; '.join(faltan) or 'nada legible'}"
    partes = [t for _, t in sorted(linea, key=lambda x: x[0])]
    if faltan:
        partes.append(f"(no se pudo leer: {'; '.join(faltan)})")
    return "\n\n".join(partes), ""


# ---------------------------------------------------------------- documentos

# Un `cm` con altura negativa antes del `Do` = la imagen está guardada al revés y
# la página la da vuelta al pintarla. Sacada del PDF así, sale de cabeza y el
# modelo no lee ni un renglón. (Freeform/Miro exportan justo así.)
RX_DIBUJA = re.compile(r"(-?[\d.]+)\s+0\s+0\s+(-?[\d.]+)\s+-?[\d.]+\s+-?[\d.]+\s*cm\s*/(\w+)\s+Do")


def _matrices(pagina) -> list[tuple[str, str, str]]:
    """[(ancho, alto, nombre)] de cada imagen pintada en la página."""
    try:
        return RX_DIBUJA.findall(bytes(pagina.get_contents().get_data()).decode("latin-1"))
    except Exception:
        return []


def _aplanar(datos: bytes, invertida: bool, destino: Path) -> Path:
    """Imagen cruda de un PDF → algo que un modelo de visión pueda leer: el dibujo
    suele venir en el canal alfa sobre negro (se ve todo negro si no se aplana),
    del revés, y a 4096px de ancho."""
    Image = _opcional("PIL.Image")
    if not Image:
        destino.write_bytes(datos)
        return destino
    import io
    im = Image.open(io.BytesIO(datos))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fondo = Image.new("RGB", im.size, "white")
        fondo.paste(im, mask=im.split()[3])
        im = fondo
    im = im.convert("RGB")
    if invertida:
        im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    im.thumbnail((1400, 1400))
    salida = destino.with_suffix(".jpg")
    im.save(salida, quality=88)
    return salida


def leer_pdf(cfg: dict, p: Path) -> tuple[str, str]:
    """Capa de texto si la hay; si no —escaneo, pizarra exportada, diagrama— se
    miran las imágenes que trae dentro. Sin esa segunda vía, un PDF sin texto
    entraba a la memoria como un nombre de archivo y nada más."""
    pypdf = _opcional("pypdf")
    if not pypdf:
        return "", "PDF sin leer — falta pypdf (pip install pypdf)"
    try:
        paginas = pypdf.PdfReader(str(p)).pages
        texto = "\n".join(pg.extract_text() or "" for pg in paginas).strip()
    except Exception as e:
        return "", f"PDF ilegible ({type(e).__name__})"
    if texto:
        return texto, ""
    invertidas = {n for pg in paginas for _, alto, n in _matrices(pg) if float(alto) < 0}
    partes, falta = [], ""
    with tempfile.TemporaryDirectory() as tmp:
        crudas = [(pg_i, im) for pg_i, pg in enumerate(paginas, 1) for im in pg.images][:IMAGENES_PDF]
        for i, (pg_i, im) in enumerate(crudas, 1):
            f = _aplanar(im.data, Path(im.name).stem in invertidas, Path(tmp) / f"p{i}.png")
            texto_im, falta = describir_imagen(
                cfg, f, f"Imagen {i} de {len(crudas)} de un PDF sin capa de texto (página {pg_i}). {PROMPT_IMAGEN}")
            if not texto_im:
                break              # sin visión, las demás fallan igual
            partes.append(texto_im)
    if partes:
        return "\n\n".join(partes), ""
    return "", f"PDF sin capa de texto y sin poder verlo: {falta or 'no trae imágenes legibles'}"


# ---------------------------------------------------------------- entrada única

def condensar(cfg: dict, texto: str) -> str:
    """Texto largo → texto que entra en un prompt, resumiendo por tramos.

    Un video de una hora deja una línea de tiempo de 40.000 caracteres. Esa línea
    va ENTERA al cuerpo de la entrada —es la única copia de lo que pasó ahí y lo
    que hace buscable el video— pero no entra en un pedido al modelo. Así que se
    corta por tramos, se resume cada uno y se pegan los resúmenes.

    Debajo de MAX_CHARS no hace nada, para que quien llama no tenga que preguntar.
    Y nunca levanta excepción: si el resumen falla, recorta y sigue — perder
    detalle es malo, perder la catalogación entera es peor.

    ponytail: una sola pasada. Si el pegado de resúmenes vuelve a pasarse de
    MAX_CHARS (haría falta un video de ~10 horas), acá va la recursión.
    """
    if len(texto) <= MAX_CHARS:
        return texto
    tramos, actual = [], ""
    for linea in texto.splitlines(keepends=True):
        if len(actual) + len(linea) > MAX_CHARS and actual:
            tramos.append(actual)
            actual = ""
        actual += linea
    if actual:
        tramos.append(actual)
    try:
        cliente = llm_mod.crear(cfg)
        fuera = []
        for i, tramo in enumerate(tramos, 1):
            _log(cfg, f"condensando tramo {i}/{len(tramos)}")
            fuera.append(cliente.completar(PROMPT_CONDENSAR,
                                           [{"role": "user", "content": tramo}], None)["texto"].strip())
        return "\n\n".join(x for x in fuera if x) or texto[:MAX_CHARS]
    except Exception as e:
        _log(cfg, f"condensar falló ({type(e).__name__}), se recorta")
        return f"{texto[:MAX_CHARS]}\n\n(… recortado: son {len(texto)} caracteres y no se pudo resumir)"


def extraer(cfg: dict, root: Path, adjunto: str) -> tuple[str, str, bool]:
    """(texto, faltante, derivado).

    `derivado` = el texto NO existe en ningún otro lado (salió de mirar una imagen
    u oír un audio) y por eso hay que guardarlo dentro de la entrada. Un .txt
    adjunto es su propio texto: duplicarlo en la entrada no agrega nada.

    Lo derivado sale ENTERO: es la única copia y va al cuerpo de la entrada. Quien
    se lo pase a un modelo lo pasa por `condensar` — son dos destinos distintos y
    recortar acá los cortaba a los dos.
    """
    if not adjunto:
        return "", "", False
    p = root / adjunto
    nombre = Path(adjunto).name
    if not p.is_file():
        return "", f"{nombre}: el archivo no está en la base", False
    ext = p.suffix.lower()
    if ext in TEXTOS:
        return p.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS], "", False
    if ext in IMAGENES:
        texto, falta = describir_imagen(cfg, p)
    elif ext in VIDEO:
        texto, falta = describir_video(cfg, p)
    elif ext in AUDIO:
        texto, falta = transcribir(cfg, p)
        if texto:
            texto = f"Transcripción del audio: {texto}"
    elif ext in DOCS:
        texto, falta = leer_pdf(cfg, p)
    else:
        return "", f"{nombre}: formato no soportado ({ext})", False
    return (texto, "", True) if texto else ("", f"{nombre}: {falta}", True)


def demo():
    """Autocheck sin LLM ni red: lo frágil acá es ffmpeg y la elección de modelo."""
    import shutil
    assert _del_endpoint({"proveedor": "claude_code", "modelo": "sonnet"}, "i") is None
    # el modelo ya capaz se usa tal cual, sin pedirle el catálogo al proveedor
    vl = {"proveedor": "openai", "modelo": "qwen/qwen3-vl-8b", "base_url": "http://nada.invalido/v1"}
    assert _del_endpoint(vl, "i") is vl
    # uno sin visión y con endpoint muerto: no explota, avisa
    assert _del_endpoint({**vl, "modelo": "openai/gpt-oss-20b"}, "i") is None
    # …pero si OTRO agente configurado sí puede ver, se usa ese (foto compartida
    # a un chat que atiende claude_code)
    ags = {"agentes": [{"id": "a", "proveedor": "claude_code", "modelo": "sonnet", "base_url": "", "api_key_env": ""},
                       {"id": "b", "proveedor": "openai", "modelo": "qwen/qwen3-vl-8b",
                        "base_url": "http://nada.invalido/v1", "api_key_env": ""}]}
    import unittest.mock as mock
    from . import agentes
    with mock.patch.object(agentes, "cargar", lambda cfg, path=None: ags):
        c = _con_modelo({"proveedor": "claude_code", "modelo": "sonnet"}, "i")
        assert c and c["modelo"] == "qwen/qwen3-vl-8b", c
        assert _con_modelo({"proveedor": "claude_code", "modelo": "sonnet"}, "v") is None  # nadie ve video
    assert extraer({}, Path("."), "")[:2] == ("", "")

    # subtítulos automáticos de YouTube: tags de karaoke y el modo roll-up, que
    # repite la línea anterior en cada cue. Sin limpiar los dos, una charla sale
    # tres veces más larga y dicha a pedazos.
    vtt = ("WEBVTT\n\n"
           "00:00:01.000 --> 00:00:03.000\nmirá <c>esto</c>\n\n"
           "00:00:03.000 --> 00:00:05.000 align:start\nmirá esto\n\n"
           "01:02:03.500 --> 01:02:06.000\nun pájaro\n")
    cues = _parsear_vtt(vtt)
    assert [t for _, _, t in cues] == ["mirá esto", "un pájaro"], cues
    assert cues[0][0] == 1.0 and cues[1][0] == 3723.5, cues          # 1h02m03.5s
    assert RX_CUE.search("y ahora mirá esto de acá") and RX_CUE.search("look at the bird")
    assert not RX_CUE.search("esto es lo que pasa aquí con this")    # deícticos sueltos: no

    # condensar: debajo del tope no toca nada; arriba, un resumen por tramo
    assert condensar({}, "corto") == "corto"
    largo = "".join(f"linea {i}\n" for i in range(4000))              # ~40k caracteres
    assert len(largo) > 3 * MAX_CHARS
    class _Falso:                                                    # noqa: E306
        def completar(self, system, msgs, tools):
            return {"texto": f"R{len(msgs[0]['content'])}"}
    with mock.patch.object(llm_mod, "crear", lambda cfg: _Falso()):
        salida = condensar({}, largo)
    assert salida.count("R") == 4 and len(salida) < 100, salida
    with mock.patch.object(llm_mod, "crear", lambda cfg: 1 / 0):     # el LLM se cae
        roto = condensar({}, largo)
    assert roto.startswith("linea 0") and "recortado" in roto        # recorta, no explota

    # PDF: solo la /Im1 va del revés (altura negativa); la /Im2 va derecha
    crudo = ("q 1093 0 0 -571 36 3067\ncm /Im1 Do Q EMC q 3586 0 0 1963 1363 3255\ncm /Im2 Do Q")
    assert {n for _, alto, n in RX_DIBUJA.findall(crudo) if float(alto) < 0} == {"Im1"}

    Image = _opcional("PIL.Image")
    if Image:
        with tempfile.TemporaryDirectory() as tmp:
            import io
            # el dibujo va en el alfa sobre RGB negro, como lo exporta una pizarra:
            # sin aplanar sale todo negro y el modelo describe "una imagen vacía"
            im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            im.paste((0, 0, 0, 255), (0, 0, 64, 16))   # franja opaca ARRIBA
            buf = io.BytesIO()
            im.save(buf, "PNG")
            plano = Image.open(_aplanar(buf.getvalue(), True, Path(tmp) / "x.png"))
            lo, hi = plano.convert("L").getextrema()   # jpeg: no exige 0/255 exactos
            assert lo < 40 and hi > 215, f"quedó plano ({lo}, {hi})"
            assert plano.getpixel((32, 56))[0] < 40, "la franja no bajó: no se dio vuelta"
            assert plano.getpixel((32, 8))[0] > 215

    if not shutil.which("ffmpeg"):
        print("media ok (sin ffmpeg: fotogramas no probados)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        v = tmp / "t.mp4"
        assert _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                       "-shortest", "-pix_fmt", "yuv420p", str(v))
        assert 2.5 < _duracion(v) < 3.5, _duracion(v)
        tomas = fotogramas(v, tmp)
        assert len(tomas) == FOTOGRAMAS and all(f.stat().st_size > 0 for _, f in tomas)
        assert [round(t, 1) for t, _ in tomas] == [0.4, 1.1, 1.9, 2.6]
        # el piso no se descarta a sí mismo aunque las tomas caigan a <5s; lo que
        # llega por extra sí: 1.0 y 1.2 están pegados al piso, 2.9 también
        assert [round(t, 1) for t, _ in fotogramas(v, tmp, extra=[1.0, 1.2, 2.9])] == [0.4, 1.1, 1.9, 2.6]
        assert _subtitulos(v) == []                     # testsrc no trae subtítulos
    print("media ok")


if __name__ == "__main__":
    demo()
