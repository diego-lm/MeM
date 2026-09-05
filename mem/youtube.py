"""Un link de YouTube → un archivo de video local que `media.describir_video` sabe leer.

Pegar un link en una captura no guardaba nada: `procesar._leer_url` trae el HTML
de la página de YouTube, que no dice de qué trata el video. Acá se lo baja de
verdad —a 360p, que alcanza de sobra para mirar fotogramas y pesa poco— con los
subtítulos PEGADOS ADENTRO del mp4, así lo que sigue no tiene que saber de dónde
salió el archivo ni manejar un .vtt suelto.

El video NO entra a la base: se baja a un temporal, se lee y se tira. Lo que
queda en la memoria es la línea de tiempo (que es lo que se busca después) y el
link (que es lo que se vuelve a mirar). Un video de una hora son ~200 MB en un
vault que está en Dropbox, y ya está en YouTube.

Solo videos públicos. Para uno privado o de una cuenta hay que pasarle cookies a
yt-dlp (`cookiefile`), que son dos líneas más y un secreto que guardar; cuando
haga falta, va acá.
"""
import re
from pathlib import Path

from . import media

# youtube.com/watch?v=, youtu.be/, /shorts/, /live/, /embed/, y el m. de celular
RX_YT = re.compile(r"https?://(?:[\w-]+\.)*(?:youtube\.com/(?:watch\?|shorts/|live/|embed/)"
                   r"|youtu\.be/)", re.I)


def es_youtube(url: str) -> bool:
    return bool(RX_YT.match(url.strip()))


def bajar(url: str, destino: Path) -> tuple[Path, str]:
    """(archivo, ficha). El archivo queda en `destino` —un temporal del que llama—
    y la ficha es lo que YouTube ya sabe del video: título, canal, fecha,
    descripción. Levanta RuntimeError con un motivo legible si no se puede."""
    yt_dlp = media._opcional("yt_dlp")
    if not yt_dlp:
        raise RuntimeError("falta yt-dlp para leer links de YouTube (pip install -e .[video])")
    opciones = {
        # 360p: se mira, no se guarda. Un mp4 chico baja rápido y los fotogramas
        # salen igual (se escalan a 768 de ancho de todos modos).
        "format": "bv*[height<=360]+ba/b[height<=360]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(Path(destino) / "%(id)s.%(ext)s"),
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["es", "en"],
        # los pega adentro del mp4 y borra el .vtt suelto: media._subtitulos los
        # saca de ahí y se ahorra la hora de whisper que tarda una charla larga
        "postprocessors": [{"key": "FFmpegEmbedSubtitle"}],
        "quiet": True, "noprogress": True, "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.sanitize_info(ydl.extract_info(url, download=True))
    except Exception as e:
        motivo = str(e)
        if "Sign in" in motivo or "cookies" in motivo or "private" in motivo.lower():
            raise RuntimeError(f"YouTube pide una cuenta para ese video y MeM solo lee públicos ({url})") from e
        raise RuntimeError(f"no se pudo bajar el video de YouTube: {motivo[:200]}") from e

    archivos = sorted(Path(destino).glob(f"{info.get('id', '*')}.*"))
    video = next((f for f in archivos if f.suffix.lower() in media.VIDEO), None)
    if not video:
        raise RuntimeError(f"yt-dlp no dejó ningún video en el temporal ({[f.name for f in archivos]})")
    dur = info.get("duration") or 0
    ficha = "\n".join(x for x in [
        f"Video de YouTube: {info.get('title', '')}",
        f"Canal: {info.get('uploader', '')}" if info.get("uploader") else "",
        f"Publicado: {info.get('upload_date', '')}" if info.get("upload_date") else "",
        f"Duración: {int(dur) // 60} min {int(dur) % 60} s" if dur else "",
        f"URL: {url}",
        f"Descripción del autor:\n{(info.get('description') or '')[:2000]}",
    ] if x)
    return video, ficha


def demo():
    """Autocheck sin red: lo único frágil de acá que no necesita YouTube es saber
    qué link es de YouTube (falso positivo = se baja algo que no es)."""
    for u in ["https://www.youtube.com/watch?v=abc123",
              "https://youtu.be/abc123?t=30",
              "https://m.youtube.com/watch?v=abc",
              "https://www.youtube.com/shorts/xyz",
              "https://www.youtube.com/live/xyz",
              "http://youtube.com/embed/xyz"]:
        assert es_youtube(u), u
    for u in ["https://vimeo.com/123", "https://ejemplo.com/youtube.com/watch?v=x",
              "https://notyoutube.com/watch?v=x", "https://www.youtube.com/",
              "no es una url"]:
        assert not es_youtube(u), u
    print("youtube ok")


if __name__ == "__main__":
    demo()
