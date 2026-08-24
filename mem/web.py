"""Web para el chat: buscar y leer páginas. Sin API key ni dependencias nuevas.

ponytail: búsqueda contra el HTML de DuckDuckGo Lite (markup simple y estable) y
extracción de texto con regex. Si DDG empieza a bloquear o cambia el markup, el
reemplazo es una API de búsqueda con key — mismas dos funciones, misma interfaz.
"""
import html as html_mod
import re

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TIMEOUT = 20.0
MAX_TEXTO = 12_000          # lo que devuelve leer_web; suficiente para un artículo
MAX_RESULTADOS = 6

TOOLS = [
    {"name": "buscar_web",
     "description": "Busca en la web (DuckDuckGo). Devuelve título, URL y resumen de los primeros resultados. Usar cuando la base no tenga la respuesta, cuando se pida información actual o cuando Diego pida investigar algo.",
     "parameters": {"type": "object", "properties": {"consulta": {"type": "string"}}, "required": ["consulta"]}},
    {"name": "leer_web",
     "description": "Descarga una URL y devuelve su texto plano. Usar para leer a fondo un resultado de buscar_web o un link que dé Diego.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
]

RX_LINK = re.compile(r"href=\"(https?://[^\"]+)\"[^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>", re.S)
RX_SNIPPET = re.compile(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", re.S)
RX_BLOQUE = re.compile(r"<(script|style|noscript|svg|nav|footer|header)\b.*?</\1\s*>", re.S | re.I)
RX_TAG = re.compile(r"<[^>]+>")
RX_ESPACIOS = re.compile(r"[ \t]*\n\s*\n\s*", re.S)


def ejecutar(nombre: str, args: dict) -> str:
    if nombre == "buscar_web":
        return buscar_web(str(args.get("consulta") or ""))
    return leer_web(str(args.get("url") or ""))


def buscar_web(consulta: str) -> str:
    if not consulta.strip():
        return "(consulta vacía)"
    try:
        r = httpx.post("https://lite.duckduckgo.com/lite/", data={"q": consulta},
                       headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return f"(no se pudo buscar en la web: {type(e).__name__}: {e})"
    hits = [f"{i}. {tit}\n   {url}\n   {res}" if res else f"{i}. {tit}\n   {url}"
            for i, (url, tit, res) in enumerate(_resultados(r.text), 1)]
    return "\n".join(hits) or "(sin resultados en la web)"


def leer_web(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "(url inválida: debe empezar con http:// o https://)"
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return f"(no se pudo leer {url}: {type(e).__name__}: {e})"
    if "html" not in r.headers.get("content-type", "text/html"):
        return r.text[:MAX_TEXTO]
    return f"[{url}]\n{_texto(r.text)[:MAX_TEXTO]}"


def _resultados(pagina: str, n: int = MAX_RESULTADOS):
    """El snippet vive en un <tr> posterior al link y a veces no existe; se busca
    entre este link y el siguiente para no desalinear título y resumen."""
    links = list(RX_LINK.finditer(pagina))
    for i, m in enumerate(links[:n]):
        fin = links[i + 1].start() if i + 1 < len(links) else len(pagina)
        sn = RX_SNIPPET.search(pagina, m.end(), fin)
        yield m.group(1), _limpio(m.group(2)), _limpio(sn.group(1)) if sn else ""


def _limpio(fragmento: str) -> str:
    return " ".join(html_mod.unescape(RX_TAG.sub("", fragmento)).split())


def _texto(pagina: str) -> str:
    cuerpo = RX_BLOQUE.sub(" ", pagina)
    cuerpo = re.sub(r"<(p|div|br|li|h[1-6]|tr)\b[^>]*>", "\n", cuerpo, flags=re.I)
    lineas = [" ".join(l.split()) for l in html_mod.unescape(RX_TAG.sub("", cuerpo)).splitlines()]
    return RX_ESPACIOS.sub("\n\n", "\n".join(l for l in lineas if l))


PAGINA_DEMO = """<html><body>
  <a rel="nofollow" href="https://ejemplo.com/uno" class='result-link'>Uno &amp; medio</a>
  <td class='result-snippet'>Primer <b>resumen</b>.</td>
  <a rel="nofollow" href="https://ejemplo.com/dos" class='result-link'>Dos</a>
  <script>basura()</script><p>Hola</p><p>mundo</p>
</body></html>"""


def demo():
    """Autocheck sin red: el parseo, que es la parte frágil."""
    r = list(_resultados(PAGINA_DEMO))
    assert [x[0] for x in r] == ["https://ejemplo.com/uno", "https://ejemplo.com/dos"], r
    assert r[0][1] == "Uno & medio" and r[0][2] == "Primer resumen."
    assert r[1][2] == "", "un resultado sin snippet no debe robar el del vecino"
    t = _texto(PAGINA_DEMO)
    assert "basura" not in t and "Hola" in t and "mundo" in t, t
    assert leer_web("ftp://x").startswith("(url inválida")
    assert buscar_web("  ") == "(consulta vacía)"
    print("web ok")


if __name__ == "__main__":
    demo()
