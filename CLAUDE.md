# MeM

App personal de Diego: backend FastAPI (`mem/`) + PWA sin build step (`mem/static/`, Preact+HTM vendorizado) + ícono de bandeja (`scripts/mem_tray.py`) + CLI (`mem/cli.py`). Arquitectura y diseño interno completos en [README.md](README.md); formato del vault en [FORMATO_MEMORIA.md](FORMATO_MEMORIA.md).

## Mantener MANUAL.md al día

[MANUAL.md](MANUAL.md) es la guía de uso (instalación, arranque, comandos `mem`, configuración, conectar a Claude, solución de problemas). Cada cambio que afecte algo que el manual documenta —un comando o flag, un paso de `config.toml`, un modo de vista, `install.ps1`, cómo se conecta MeM a Claude— actualizalo en el mismo cambio, no como tarea aparte. Si el cambio es interno (no visible al usar la app) va a README.md, no al manual; no dupliques lo mismo en los dos.

## install.ps1

Es el instalador unificado: bootstrap en máquina nueva (`irm .../install.ps1 | iex`) o actualización in-place (`.\install.ps1` corrido desde un checkout — detecta que ya está dentro del repo por `pyproject.toml` en `$PSScriptRoot` y hace `git pull` en vez de clonar). Si cambian los pasos de instalación (dependencias, extras opcionales, `config.toml`, autostart), actualizar el script y `MANUAL.md` juntos.

## Secretos

`config.toml` (real, con `mcp_secreto` y rutas de esta máquina) está en `.gitignore` — nunca se comitea. `config.example.toml` es la plantilla pública, sin secretos ni rutas personales; si `config.py → DEFAULTS` gana una clave nueva, sumarla también ahí.
