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

FOTOGRAMAS = 4          # ponytail: 4 tomas bastan para "de qué trata"; subir si hace falta detalle temporal
IMAGENES_PDF = 8        # tope de imágenes a describir en un PDF sin capa de texto
MAX_CHARS = 12_000      # tope por adjunto: lo que entra al prompt y al cuerpo de la entrada
WHISPER = "small"       # ponytail: modelo faster-whisper por defecto; "medium"/"large-v3" si falla el español

PROMPT_IMAGEN = (
    "Describe en español, con TODO el detalle que puedas, el contenido de esta imagen: "
    "qué se ve (objetos, personas, lugar, marcas, colores, acciones), TODO el texto legible "
    "transcrito literalmente, y de qué trata. Esta descripción REEMPLAZA a la imagen dentro de "
    "una memoria personal de solo texto: lo que no escribas se pierde para siempre. "
    "No inventes lo que no se ve.")


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

def describir_imagen(cfg: dict, p: Path, prompt: str = PROMPT_IMAGEN) -> tuple[str, str]:
    c = _con_modelo(cfg, "i")
    if not c:
        return "", f"ningún modelo con visión en {_endpoint(cfg)}"
    try:
        return llm_mod.describir_imagen(c, p, prompt).strip(), ""
    except Exception as e:
        return "", f"{c['modelo']} no pudo leer la imagen ({type(e).__name__})"


# ---------------------------------------------------------------- audio

_DEVICE = "auto"        # baja a "cpu" en cuanto se comprueba que CUDA está a medias


@lru_cache(maxsize=2)   # 2: el "auto" que falló y el "cpu" que lo reemplaza
def _whisper(modelo: str, device: str):
    fw = _opcional("faster_whisper")
    return fw.WhisperModel(modelo, device=device, compute_type="int8") if fw else None


def transcribir(cfg: dict, p: Path) -> tuple[str, str]:
    """Audio → texto. Primero faster-whisper si está instalado (local, offline);
    si no, el endpoint estándar /v1/audio/transcriptions del proveedor.

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
        # ponytail: en CPU int8 "small" va a ~1x tiempo real (medido: 7.9s para 7s de
        # voz), que alcanza para notas de voz y para los clips cortos que genera H3.
        # Si algún día hay que transcribir una hora, la salida es instalar
        # nvidia-cublas-cu12 + nvidia-cudnn-cu12 y abrirle sus DLL a ctranslate2 con
        # os.add_dll_directory — no vale la pena hasta que moleste de verdad.
        # Y va envuelto porque un ASR roto no puede tumbar la catalogación entera:
        # el contrato de este módulo es devolver (texto, motivo), nunca explotar.
        falla = ""
        for device in dict.fromkeys([_DEVICE, "cpu"]):
            try:
                segs, _ = _whisper(WHISPER, device).transcribe(str(p))
                _DEVICE = device
                return " ".join(s.text.strip() for s in segs).strip(), ""
            except Exception as e:
                falla = f"faster-whisper no pudo transcribir en {device} ({type(e).__name__}: {e})"
        return "", falla[:300]
    base = cfg.get("base_url")
    if base:
        try:
            import os

            from openai import OpenAI
            asr = next((m["id"] for m in llm_mod.modelos(cfg) if m["in"] == "a"), "whisper-1")
            cliente = OpenAI(base_url=base, api_key=os.environ.get(cfg.get("api_key_env", ""), "") or "lm-studio")
            with p.open("rb") as f:
                return (cliente.audio.transcriptions.create(model=asr, file=f).text or "").strip(), ""
        except Exception:
            pass
    return "", ("sin transcripción — instalar faster-whisper (pip install faster-whisper) "
                f"o apuntar el proveedor a un servidor con /v1/audio/transcriptions ({_endpoint(cfg)} no lo tiene)")


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


def fotogramas(p: Path, destino: Path, n: int = FOTOGRAMAS) -> list[tuple[float, Path]]:
    """n tomas repartidas por la duración, al centro de cada tramo (el frame 0 de
    un video suele ser negro). Devuelve [(segundo, jpg)] de lo que se pudo sacar."""
    dur = _duracion(p)
    if dur <= 0:
        return []
    out = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        f = destino / f"f{i}.jpg"
        # -ss antes de -i = seek rápido por keyframe; suficiente para un resumen
        if _ffmpeg("-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1",
                   "-vf", "scale='min(768,iw)':-2", str(f)) and f.is_file():
            out.append((t, f))
    return out


def describir_video(cfg: dict, p: Path) -> tuple[str, str]:
    """Video → texto: lo que se ve (fotogramas por visión) + lo que se oye
    (pista de audio transcrita). Si solo una de las dos sale, se guarda esa y la
    otra se reporta como faltante — medio contenido es mejor que ninguno."""
    if not shutil.which("ffmpeg"):
        return "", "video sin procesar — falta ffmpeg en el PATH"
    partes, faltan = [], []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tomas = fotogramas(p, tmp)
        if not tomas:
            faltan.append("no se pudieron extraer fotogramas")
        for i, (t, f) in enumerate(tomas, 1):
            texto, falta = describir_imagen(
                cfg, f, f"Fotograma {i} de {len(tomas)} de un video, en el segundo {t:.0f}. {PROMPT_IMAGEN}")
            if texto:
                partes.append(f"[{t:.0f}s] {texto}")
            elif falta and falta not in faltan:
                faltan.append(falta)
                break                     # sin visión, los demás fotogramas fallan igual
        wav = tmp / "audio.wav"
        if _ffmpeg("-i", str(p), "-vn", "-ac", "1", "-ar", "16000", str(wav)) and wav.is_file():
            dicho, falta = transcribir(cfg, wav)
            if dicho:
                partes.append(f"Transcripción del audio: {dicho}")
            elif falta:
                faltan.append(falta)
    if not partes:
        return "", f"video ({p.suffix}): {'; '.join(faltan) or 'nada legible'}"
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

def extraer(cfg: dict, root: Path, adjunto: str) -> tuple[str, str, bool]:
    """(texto, faltante, derivado).

    `derivado` = el texto NO existe en ningún otro lado (salió de mirar una imagen
    u oír un audio) y por eso hay que guardarlo dentro de la entrada. Un .txt
    adjunto es su propio texto: duplicarlo en la entrada no agrega nada.
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
    return (texto[:MAX_CHARS], "", True) if texto else ("", f"{nombre}: {falta}", True)


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
    print("media ok")


if __name__ == "__main__":
    demo()
