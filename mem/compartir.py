"""Recibir lo que el móvil comparte con MeM (Web Share Target).

Android/Chrome mandan un POST `multipart/form-data` al `action` que declara el
manifest, con los campos que ahí se piden (title, text, url, archivos). Como el
POST lo hace el propio navegador desde la hoja de compartir, no puede llevar
cabeceras: llega crudo y sin token.

El multipart se parsea con el parser MIME de la STDLIB —multipart/form-data ES
MIME— en vez de sumar python-multipart: `/attach` ya evita esa dependencia
subiendo el cuerpo crudo, y esto es la otra mitad del mismo problema.

Lo recibido va a un buzón en memoria y el server redirige a `#share/<id>`: la
app lo recoge (GET, ya con token) y decide a qué sesión mandarlo. No se persiste
nada más que los archivos, que van al inbox como cualquier otro adjunto.
"""
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

from .memoria import slugificar

BUZON_MAX = 8       # lo compartido se recoge en el acto; el tope es solo por si no


def partes(cuerpo: bytes, content_type: str) -> list[dict]:
    """[{nombre, archivo, datos}] de un cuerpo multipart. Vacío si no lo es."""
    if "multipart/" not in content_type.lower():
        return []
    # el parser necesita la cabecera Content-Type (trae el boundary) pegada al cuerpo
    msg = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\n\r\n" + cuerpo)
    if not msg.is_multipart():
        return []
    return [{"nombre": p.get_param("name", "", header="content-disposition"),
             "archivo": p.get_filename() or "",
             "datos": p.get_payload(decode=True) or b""}
            for p in msg.iter_parts()]


def _texto(ps: list[dict]) -> str:
    """title + text + url, sin repetir la url si ya venía dentro del texto
    (WhatsApp manda todo junto en `text`; Chrome los separa)."""
    campos = {p["nombre"]: p["datos"].decode("utf-8", "replace").strip()
              for p in ps if not p["archivo"]}
    partes_txt = [campos.get("title", ""), campos.get("text", "")]
    url = campos.get("url", "")
    if url and url not in campos.get("text", ""):
        partes_txt.append(url)
    return "\n".join(p for p in partes_txt if p).strip()


def _guardar(root: Path, ps: list[dict]) -> list[str]:
    """Los archivos compartidos, al mismo sitio que los adjuntos de la app."""
    destino = Path(root) / "07_Inbox/_adjuntos"
    rutas = []
    for i, p in enumerate(x for x in ps if x["archivo"] and x["datos"]):
        n = Path(p["archivo"])
        seguro = (slugificar(n.stem) or "compartido") + n.suffix
        rel = f"07_Inbox/_adjuntos/{datetime.now():%Y-%m-%d_%H%M%S}_{i}_{seguro}"
        destino.mkdir(parents=True, exist_ok=True)
        (Path(root) / rel).write_bytes(p["datos"])
        rutas.append(rel)
    return rutas


_buzon: dict[str, dict] = {}


def recibir(root: Path, cuerpo: bytes, content_type: str) -> str:
    """Guarda lo compartido y devuelve el id con el que la app va a recogerlo."""
    ps = partes(cuerpo, content_type)
    texto, rutas = _texto(ps), _guardar(root, ps)
    # un turno lleva UN adjunto: el resto queda guardado y nombrado en el texto,
    # así compartir 3 fotos no pierde 2
    if len(rutas) > 1:
        texto = (texto + "\n\n[también se guardaron: "
                 + ", ".join(r.split("/")[-1] for r in rutas[1:]) + "]").strip()
    ident = f"{datetime.now():%H%M%S%f}"
    _buzon[ident] = {"texto": texto, "adjunto": rutas[0] if rutas else ""}
    for viejo in list(_buzon)[:-BUZON_MAX]:
        del _buzon[viejo]
    return ident


def tomar(ident: str) -> dict:
    """Lo recoge UNA vez: recargar la app no debe reenviar lo mismo."""
    return _buzon.pop(ident, {"texto": "", "adjunto": ""})


def demo():
    """Un cuerpo multipart como el que manda Android, con binario y CRLF dentro."""
    import tempfile

    b = "----memBoundary42"
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) + b"\r\n--falso--\r\n"   # trampas a propósito
    cuerpo = b"".join([
        f"--{b}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nUn título\r\n".encode(),
        f"--{b}\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\nmirá esto ñ áé\r\n".encode(),
        f"--{b}\r\nContent-Disposition: form-data; name=\"url\"\r\n\r\nhttps://ej.com/a\r\n".encode(),
        f"--{b}\r\nContent-Disposition: form-data; name=\"archivos\"; filename=\"Foto Ñ 1.png\"\r\n"
        f"Content-Type: image/png\r\n\r\n".encode(), png, b"\r\n",
        f"--{b}\r\nContent-Disposition: form-data; name=\"archivos\"; filename=\"b.png\"\r\n"
        f"Content-Type: image/png\r\n\r\n".encode(), b"xy", b"\r\n",
        f"--{b}--\r\n".encode(),
    ])
    ct = f'multipart/form-data; boundary="{b}"'

    ps = partes(cuerpo, ct)
    assert len(ps) == 5, ps
    assert [p["nombre"] for p in ps] == ["title", "text", "url", "archivos", "archivos"]
    assert ps[3]["datos"] == png, "el binario tiene que salir byte a byte"
    assert ps[3]["archivo"] == "Foto Ñ 1.png"
    assert partes(b"lo que sea", "application/json") == []

    assert _texto(ps) == "Un título\nmirá esto ñ áé\nhttps://ej.com/a"
    # url ya dentro del texto: no se repite
    solo = [{"nombre": "text", "archivo": "", "datos": b"vean https://ej.com/a"},
            {"nombre": "url", "archivo": "", "datos": b"https://ej.com/a"}]
    assert _texto(solo) == "vean https://ej.com/a"

    with tempfile.TemporaryDirectory() as tmp:
        ident = recibir(Path(tmp), cuerpo, ct)
        d = tomar(ident)
        assert d["adjunto"].startswith("07_Inbox/_adjuntos/") and d["adjunto"].endswith("_foto-n-1.png"), d
        assert (Path(tmp) / d["adjunto"]).read_bytes() == png
        assert "también se guardaron: " in d["texto"] and "_b.png" in d["texto"]
        assert tomar(ident) == {"texto": "", "adjunto": ""}, "se recoge una sola vez"
    print("compartir ok")


if __name__ == "__main__":
    demo()
