# MeM

App personal de Diego: backend FastAPI (`mem/`) + PWA sin build step (`mem/static/`, Preact+HTM vendorizado) + ícono de bandeja (`scripts/mem_tray.py`) + CLI (`mem/cli.py`). Arquitectura y diseño interno completos en [README.md](README.md); formato del vault en [FORMATO_MEMORIA.md](FORMATO_MEMORIA.md).

## Mantener MANUAL.md al día

[MANUAL.md](MANUAL.md) es la guía de uso (instalación, arranque, comandos `mem`, configuración, conectar a Claude, solución de problemas). Cada cambio que afecte algo que el manual documenta —un comando o flag, un paso de `config.toml`, un modo de vista, `install.ps1`, cómo se conecta MeM a Claude— actualizalo en el mismo cambio, no como tarea aparte. Si el cambio es interno (no visible al usar la app) va a README.md, no al manual; no dupliques lo mismo en los dos.

## install.ps1

Es el instalador unificado: bootstrap en máquina nueva (`irm .../install.ps1 | iex`) o actualización in-place (`.\install.ps1` corrido desde un checkout — detecta que ya está dentro del repo por `pyproject.toml` en `$PSScriptRoot` y hace `git pull` en vez de clonar). Si cambian los pasos de instalación (dependencias, extras opcionales, `config.toml`, autostart), actualizar el script y `MANUAL.md` juntos.

`Instalar-MeM.bat` es el doble clic: llama a `install.ps1` con `-ExecutionPolicy Bypass` (solo para ese proceso) y hace `pause` al final para que se lea el resultado. Deliberadamente NO es un `.exe`: uno sin firmar dispara SmartScreen y habría que recompilarlo en cada cambio del `.ps1`.

Tres cosas que ya costaron encontrarlas y no hay que deshacer:

- `install.ps1` va con **BOM UTF-8**. Sin él, Windows PowerShell 5.1 lo lee con el codepage del sistema y los acentos rompen el parser en líneas que ni los tienen.
- Los `.bat` van con **CRLF y sin BOM**, forzado por `.gitattributes`. `cmd.exe` los lee buscando por posición y con LF se le corre el offset y se come el primer carácter de una línea (`REM` → `EM`). Depende del tamaño del archivo, así que uno corto parece andar y el de al lado no — no confiar en el `core.autocrlf` de cada máquina.
- **El primer `python` del PATH no es confiable**: puede ser el venv de otro proyecto, así que se filtran los `\venv\` y se pregunta la versión.

## El repo es PÚBLICO

Desde el 2026-08-24. Lo que se comitea acá lo lee cualquiera, así que antes de agregar un ejemplo con una URL, un host o un mail: los datos personales van a `config.toml` (gitignoreado) o a una variable de entorno, y en el repo queda un placeholder. Precedentes: el host del tailnet es `tu-maquina.tu-tailnet.ts.net` en README y `mem-plugin/.mcp.json`, y el contacto de Nominatim sale de `MEM_CONTACTO` (`indice.py`), no del código.

Y ojo con el historial: reescribirlo NO borra nada de GitHub — los commits viejos se siguen sirviendo por SHA aunque queden fuera de toda rama (comprobado). Si alguna vez se cuela un secreto en un commit, el arreglo real es rotar el secreto, no reescribir.

## Componentes (winget)

`componentes.json` en la raíz es el catálogo de lo que MeM puede instalar y desinstalar por winget (LM Studio, Ollama, ComfyUI). Lo leen `mem/componentes.py` (API `/components`, pantalla Ajustes › Media › Componentes) **e** `install.ps1`: agregar un componente es agregarlo ahí, sin tocar código ni la UI.

El motor es winget y nada más — no escribir descargadores, ni registro de versiones, ni desempaquetado. `install.ps1` no repite la detección: le pregunta a `mem.componentes` (por eso el paso va después del `pip install`), así "instalado a mano" significa lo mismo en los dos lados.

Tres estados y no dos: `winget` (se puede desinstalar), `manual` (está pero no lo puso winget — no se ofrece sacarlo, winget devuelve un error ilegible) y `no`. La barrera del estado `manual` está en `_correr`, no solo en la UI.

## Secretos

`config.toml` (real, con `mcp_secreto` y rutas de esta máquina) está en `.gitignore` — nunca se comitea. `config.example.toml` es la plantilla pública, sin secretos ni rutas personales; si `config.py → DEFAULTS` gana una clave nueva, sumarla también ahí.
