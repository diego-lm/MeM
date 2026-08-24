"""Cliente MCP mínimo (stdio, JSON-RPC 2.0): expone al chat las herramientas de
servidores MCP externos.

Los servidores se declaran en mcp.json en la raíz del repo, con el mismo formato
que usa Claude Code, así se puede pegar cualquier config que ande dando vueltas:

  {"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "W:/..."]}}}

Sin mcp.json no hay servidores y esto no cuesta nada.

ponytail: solo transporte stdio y solo `tools` (ni resources ni prompts); un
proceso vivo por servidor reutilizado entre turnos. Agregar SSE/HTTP el día que
haga falta un servidor remoto.
"""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent.parent / "mcp.json"
TIMEOUT = 30.0
ARRANQUE = 60.0             # npx puede tardar en bajar el paquete la primera vez
PROTOCOLO = "2024-11-05"
MAX_SALIDA = 8_000

_servidores: dict[str, "Servidor"] = {}


def config() -> dict:
    if not ARCHIVO.exists():
        return {}
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8")).get("mcpServers") or {}
    except (json.JSONDecodeError, OSError):
        return {}


class Servidor:
    """Un proceso MCP hablando JSON-RPC por stdin/stdout, líneas de JSON."""

    def __init__(self, nombre: str, spec: dict):
        self.nombre = nombre
        # timeout por servidor (mcp.json): una generación de video tarda minutos
        self.timeout = float(spec.get("timeout") or TIMEOUT)
        self.n = 0
        self.ex = ThreadPoolExecutor(max_workers=1)   # serializa las peticiones
        self.p = subprocess.Popen(
            [spec["command"], *(spec.get("args") or [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env={**os.environ, **(spec.get("env") or {})}, cwd=spec.get("cwd") or None)
        self.pedir("initialize", {"protocolVersion": PROTOCOLO, "capabilities": {},
                                  "clientInfo": {"name": "MeM", "version": "1"}}, ARRANQUE)
        self._enviar({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self.tools = self.pedir("tools/list", {}).get("tools", [])

    def _enviar(self, msg: dict) -> None:
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def _rpc(self, metodo: str, params: dict) -> dict:
        self.n += 1
        self._enviar({"jsonrpc": "2.0", "id": self.n, "method": metodo, "params": params})
        while linea := self.p.stdout.readline():
            try:
                msg = json.loads(linea)
            except json.JSONDecodeError:
                continue                                  # ruido en stdout: se ignora
            if msg.get("id") != self.n:
                continue                                  # notificaciones del servidor
            if "error" in msg:
                raise RuntimeError(str(msg["error"].get("message") or msg["error"]))
            return msg.get("result") or {}
        raise RuntimeError("el servidor cerró la conexión")

    def pedir(self, metodo: str, params: dict, timeout: float = 0) -> dict:
        # readline() no se puede interrumpir: si el servidor se cuelga, se mata.
        timeout = timeout or self.timeout
        try:
            return self.ex.submit(self._rpc, metodo, params).result(timeout=timeout)
        except FutTimeout:
            self.cerrar()
            raise RuntimeError(f"sin respuesta en {timeout:.0f}s")

    def cerrar(self) -> None:
        _servidores.pop(self.nombre, None)
        self.p.kill()
        self.ex.shutdown(wait=False, cancel_futures=True)


def _servidor(nombre: str, spec: dict) -> Servidor:
    srv = _servidores.get(nombre)
    if srv and srv.p.poll() is None:
        return srv
    _servidores[nombre] = Servidor(nombre, spec)
    return _servidores[nombre]


def tools() -> list[dict]:
    """Herramientas de todos los servidores declarados, en el formato del motor.
    Un servidor caído se salta: no puede romper el chat."""
    out = []
    for nombre, spec in config().items():
        try:
            srv = _servidor(nombre, spec)
        except Exception:
            continue
        for t in srv.tools:
            out.append({"name": f"mcp__{nombre}__{t['name']}",
                        "description": f"[{nombre}] {(t.get('description') or '')[:400]}",
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}})
    return out


def llamar(nombre_completo: str, args: dict) -> str:
    partes = nombre_completo.split("__", 2)
    if len(partes) != 3:
        return f"(nombre de herramienta MCP inválido: {nombre_completo})"
    _, servidor, herramienta = partes
    spec = config().get(servidor)
    if not spec:
        return f"(servidor MCP desconocido: {servidor})"
    try:
        r = _servidor(servidor, spec).pedir("tools/call", {"name": herramienta, "arguments": args})
    except Exception as e:
        return f"(error MCP {servidor}/{herramienta}: {type(e).__name__}: {e})"
    return _texto(r)


def _texto(r: dict) -> str:
    partes = [str(c.get("text") or "") for c in (r.get("content") or []) if c.get("type") == "text"]
    salida = "\n".join(p for p in partes if p) or json.dumps(r, ensure_ascii=False)
    return salida[:MAX_SALIDA]


# servidor de juguete para el autocheck: habla el protocolo, no hace nada útil
SERVIDOR_DEMO = r"""
import json, sys
for linea in sys.stdin:
    m = json.loads(linea)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2024-11-05", "capabilities": {}}
    elif m["method"] == "tools/list":
        r = {"tools": [{"name": "eco", "description": "repite en mayúsculas",
                        "inputSchema": {"type": "object", "properties": {"t": {"type": "string"}}}}]}
    else:
        r = {"content": [{"type": "text", "text": m["params"]["arguments"]["t"].upper()}]}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}) + "\n")
    sys.stdout.flush()
"""


def demo():
    """Autocheck end-to-end contra un servidor MCP de juguete: handshake, listado
    y llamada. Sin red y sin depender de que haya un mcp.json real."""
    global config
    real = config
    config = lambda: {"demo": {"command": sys.executable, "args": ["-c", SERVIDOR_DEMO]}}
    try:
        ts = tools()
        assert [t["name"] for t in ts] == ["mcp__demo__eco"], ts
        assert llamar("mcp__demo__eco", {"t": "hola"}) == "HOLA"
        assert llamar("mcp__fantasma__x", {}).startswith("(servidor MCP desconocido")
        assert llamar("suelto", {}).startswith("(nombre de herramienta MCP inválido")
        _servidores["demo"].cerrar()
    finally:
        config = real
    assert tools() == [] or ARCHIVO.exists(), "sin mcp.json no debe haber tools"
    print("mcps ok")


if __name__ == "__main__":
    demo()
