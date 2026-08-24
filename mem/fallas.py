"""Un fallo del turno dicho en dos líneas: qué pasó y qué puede hacer Diego.

Existe por lo que llegó al chat el 2026-08-07: el str() crudo de un
subprocess.TimeoutExpired, o sea la LÍNEA DE COMANDOS entera del CLI de Claude
—con el system prompt de 2.000 caracteres adentro— como mensaje de error.
Ilegible, larguísimo y sin una sola pista de qué hacer.

Acá cada fallo se traduce a (problema, sugerencia) y el texto crudo queda como
`detalle`, acotado, para el bloque plegable de la burbuja.
"""
import re
import subprocess

MAX_DETALLE = 300

# (patrón, problema, qué hacer). Gana el primero que coincide: lo específico
# arriba, y "connect" antes que "timeout" porque un ConnectTimeout es el
# servicio caído, no un turno lento.
CASOS = (
    (r"ComfyUI no está corriendo",
     "ComfyUI no está corriendo.",
     "Pedile al chat que lo arranque («arrancá ComfyUI») o abrilo a mano y reintentá."),
    (r"no existe el workflow",
     "Falta el workflow de ComfyUI para eso.",
     "Exportalo desde ComfyUI en formato API a 09_Sistema/Workflows/ y reintentá."),
    (r"Not authenticated|auth login",
     "Higgsfield no tiene la sesión iniciada.",
     "Corré `higgsfield auth login` en una terminal y reintentá."),
    (r"[Nn]o workspace selected|workspace",
     "Higgsfield no tiene workspace elegido.",
     "Corré `higgsfield workspace list` y después `higgsfield workspace set <id>`."),
    (r"no se encontró el comando 'claude'",
     "Claude Code no está instalado en esta máquina.",
     "Instalalo, o pasá el agente a uno de API o local en Ajustes › Agentes."),
    (r"connect|refused|unreachable|sin conexión",
     "No hay nadie escuchando donde vive el agente.",
     "Si es el modelo local, arrancá LM Studio con el botón de acá abajo; "
     "si no, revisá la URL del agente en Ajustes › Agentes."),
    # el CLI de Claude Code contra una generación larga: no entra en su ventana
    (r"no respondió en|TimeoutExpired|timed out|timeout",
     "El agente tardó más de lo que la app puede esperar.",
     "Si era una generación larga, pedila otra vez: el taller (modo media) la atiende "
     "con el agente de «crear», que sí puede esperar."),
    (r"credit balance|insufficient|billing|quota",
     "La cuenta de ese proveedor se quedó sin saldo.",
     "Reintentá con el otro agente, o revisá el plan del proveedor."),
    (r"\b429\b|rate.?limit|overloaded",
     "El proveedor está saturado o llegaste a su límite.",
     "Esperá un momento y reintentá, o usá el otro agente."),
    (r"\b40[13]\b|unauthorized|invalid.*api.?key|authentication",
     "El proveedor rechazó las credenciales.",
     "Revisá la API key del agente en Ajustes › Agentes."),
)

GENERICO = ("El turno no pudo terminar.",
            "Reintentá; si vuelve a pasar, probá con el otro agente.")


def _crudo(e) -> str:
    if isinstance(e, str):
        return e
    if isinstance(e, subprocess.TimeoutExpired):
        # su str() ES el argv entero, y ahí adentro viaja el system prompt: nunca se
        # muestra, ni recortado. Lo único que dice algo es cuánto se esperó.
        return f"TimeoutExpired: {e.cmd[0] if e.cmd else 'el proceso'} no respondió en {e.timeout:.0f}s"
    return f"{type(e).__name__}: {e}"


def explicar(e) -> dict:
    """{problema, sugerencia, detalle} de una excepción o de un texto de error."""
    crudo = _crudo(e)
    # una sola línea y acotado: el detalle es para mirarlo plegado, no para llenar
    # la pantalla (y así ningún argv gigante entra entero aunque se cuele uno)
    detalle = " ".join(str(crudo).split())[:MAX_DETALLE]
    problema, sugerencia = next(((p, s) for rx, p, s in CASOS if re.search(rx, crudo, re.I)), GENERICO)
    return {"problema": problema, "sugerencia": sugerencia, "detalle": detalle}


def demo():
    """Lo frágil acá es el orden de la tabla: un caso genérico arriba se come a
    los específicos y la sugerencia pasa a ser inútil."""
    # el fallo que motivó el módulo: el argv NO puede llegar a nadie, ni recortado
    # (ahí adentro viaja el system prompt entero)
    e = subprocess.TimeoutExpired(["claude.EXE", "-p", "--append-system-prompt", "x" * 3000], 300)
    d = explicar(e)
    assert "tardó más" in d["problema"] and "media" in d["sugerencia"]
    assert d["detalle"] == "TimeoutExpired: claude.EXE no respondió en 300s", d["detalle"]
    assert "x" * 20 not in d["detalle"]

    assert "sin saldo" in explicar("claude CLI: Credit balance is too low")["problema"]
    assert "ComfyUI no está" in explicar("(no se pudo generar: ComfyUI no está corriendo en http://x)")["problema"]
    assert "workflow" in explicar("(no se pudo generar: no existe el workflow x.json)")["problema"]
    assert "sesión iniciada" in explicar("Not authenticated")["problema"]
    # ConnectTimeout es el servicio caído, no un turno lento: gana "connect"
    assert "escuchando" in explicar("httpx.ConnectTimeout: timed out")["problema"]
    assert "escuchando" in explicar("APIConnectionError: Connection refused")["problema"]
    assert explicar("algo rarísimo") == {"problema": GENERICO[0], "sugerencia": GENERICO[1],
                                         "detalle": "algo rarísimo"}
    print("fallas ok")


if __name__ == "__main__":
    demo()
