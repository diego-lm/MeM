# MeM

App personal de Diego: backend FastAPI (`mem/`) + PWA sin build step (`mem/static/`, Preact+HTM vendorizado) + ícono de bandeja (`scripts/mem_tray.py`) + CLI (`mem/cli.py`). Arquitectura y diseño interno completos en [README.md](README.md); formato del vault en [FORMATO_MEMORIA.md](FORMATO_MEMORIA.md).

## Mantener MANUAL.md al día

[MANUAL.md](MANUAL.md) es la guía de uso (instalación, arranque, comandos `mem`, configuración, conectar a Claude, solución de problemas). Cada cambio que afecte algo que el manual documenta —un comando o flag, un paso de `config.toml`, un modo de vista, `install.ps1`, cómo se conecta MeM a Claude— actualizalo en el mismo cambio, no como tarea aparte. Si el cambio es interno (no visible al usar la app) va a README.md, no al manual; no dupliques lo mismo en los dos.

## install.ps1

Es el instalador unificado: bootstrap en máquina nueva (`irm .../install.ps1 | iex`) o actualización in-place (`.\install.ps1` corrido desde un checkout — detecta que ya está dentro del repo por `pyproject.toml` en `$PSScriptRoot` y hace `git pull` en vez de clonar). Si cambian los pasos de instalación (dependencias, extras opcionales, `config.toml`, autostart), actualizar el script y `MANUAL.md` juntos.

Dos cosas que ya costaron encontrarlas y no hay que deshacer: el archivo va con **BOM UTF-8** (sin él, Windows PowerShell 5.1 lo lee con el codepage del sistema y los acentos rompen el parser en líneas que ni los tienen), y **el primer `python` del PATH no es confiable** — puede ser el venv de otro proyecto, así que se filtran los `\venv\` y se pregunta la versión.

## Componentes (winget)

`componentes.json` en la raíz es el catálogo de lo que MeM puede instalar y desinstalar por winget (LM Studio, Ollama, ComfyUI). Lo leen `mem/componentes.py` (API `/components`, pantalla Ajustes › Media › Componentes) **e** `install.ps1`: agregar un componente es agregarlo ahí, sin tocar código ni la UI.

El motor es winget y nada más — no escribir descargadores, ni registro de versiones, ni desempaquetado. `install.ps1` no repite la detección: le pregunta a `mem.componentes` (por eso el paso va después del `pip install`), así "instalado a mano" significa lo mismo en los dos lados.

Tres estados y no dos: `winget` (se puede desinstalar), `manual` (está pero no lo puso winget — no se ofrece sacarlo, winget devuelve un error ilegible) y `no`. La barrera del estado `manual` está en `_correr`, no solo en la UI.

## Secretos

`config.toml` (real, con `mcp_secreto` y rutas de esta máquina) está en `.gitignore` — nunca se comitea. `config.example.toml` es la plantilla pública, sin secretos ni rutas personales; si `config.py → DEFAULTS` gana una clave nueva, sumarla también ahí.
