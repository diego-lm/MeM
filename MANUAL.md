# Manual de uso — MeM

Guía práctica para instalar, arrancar y usar MeM día a día. Para arquitectura interna, formato del vault y diseño del frontend, ver [README.md](README.md) y [FORMATO_MEMORIA.md](FORMATO_MEMORIA.md).

## Qué es

MeM es tu memoria personal: una base de Markdown (`hamuQ/`, en Dropbox) que capturás desde el celu o la compu, la app organiza sola (tags, temas, conexiones, búsqueda), y con la que podés chatear en **Sesiones** de trabajo. Corre local en Windows: un servidor (FastAPI) + una app web (PWA) en `http://localhost:8765`, con un ícono en la bandeja del sistema.

## Instalación

### Máquina nueva

El repositorio es **privado**, así que primero hay que darle acceso a esa máquina. En PowerShell:

```powershell
winget install GitHub.cli
```

```powershell
gh auth login
```

```powershell
gh repo clone diego-lm/MeM "$env:USERPROFILE\MeM"
```

Y ahí, doble clic en `Instalar-MeM.bat` dentro de la carpeta, o desde la misma terminal:

```powershell
& "$env:USERPROFILE\MeM\install.ps1"
```

De ahí en adelante hace todo solo: instala lo que falte (git, Python), crea el entorno virtual, instala dependencias, copia `config.example.toml` → `config.toml`, ofrece los componentes opcionales y deja un acceso directo para que MeM arranque solo al iniciar sesión.

Lo único que tiene que estar de antes es **winget** (el "Instalador de aplicaciones" de Windows), que viene con Windows 10/11; si falta, se pone desde la Microsoft Store.

> **Si el repo pasara a público**, los tres primeros pasos se reemplazan por una sola línea que no pide cuenta de GitHub — `irm https://raw.githubusercontent.com/diego-lm/MeM/main/install.ps1 | iex`. Con el repo privado esa URL devuelve 404: `raw.githubusercontent.com` no lleva credenciales.

Parámetros:
- `-Con <ids>` — instalar estos componentes sin preguntar, p. ej. `-Con lmstudio,comfyui`.
- `-SinPreguntar` — desatendido: solo MeM, ningún componente opcional.
- `-Destino <carpeta>` — instalar en otro lado (default `%USERPROFILE%\MeM`).
- `-Audio` — instala también faster-whisper (transcripción de audio local; pesado, ver [Solución de problemas](#solución-de-problemas)).
- `-SinAutostart` — no crear el acceso directo de arranque automático.

### Componentes opcionales

Ni MeM ni el instalador traen los motores de IA: los instala **winget**, el gestor de paquetes de Windows, y por eso actualizarlos o sacarlos después funciona como con cualquier otro programa.

| Componente | Para qué | Paquete |
|---|---|---|
| **LM Studio** | modelos de lenguaje locales, servidor OpenAI-compatible en `localhost:1234` | `ElementLabs.LMStudio` |
| **Ollama** | la otra vía de modelos locales, por línea de comandos; sirve en `localhost:11434/v1` | `Ollama.Ollama` |
| **ComfyUI** | generación local de imagen y video (backend `local` del modo media) | `Comfy.ComfyUI-Desktop` |

El instalador los ofrece uno por uno; después se ponen y se sacan desde **Ajustes › Media › Componentes**, sin volver a la terminal. Windows puede pedir permiso (UAC) en la PC mientras winget trabaja, aunque la orden haya salido del celular.

Un componente que instalaste **a mano** (un ComfyUI clonado a pulso, LM Studio puesto por su `.exe`) aparece como "instalado a mano" y no ofrece desinstalar: winget no puede sacar lo que no puso. Se saca por donde se puso.

Para usar Ollama desde MeM: Ajustes › Proveedor de IA › crear un agente con proveedor `openai` y `base_url` `http://localhost:11434/v1`, y el modelo que hayas bajado (`ollama pull <modelo>`).

Agregar un componente nuevo a la lista es agregarlo a [componentes.json](componentes.json) — el instalador y la app leen ese mismo archivo, no hay código que tocar.

### Actualizar un checkout existente

Doble clic en `Instalar-MeM.bat`, o desde la carpeta del repo:

```powershell
.\install.ps1
```

Al correr desde adentro de un checkout, `install.ps1` no clona de nuevo: hace `git pull`, reinstala dependencias si cambiaron y deja el autostart al día. Es el mismo comando para "instalar" y para "actualizar tras un cambio".

### Instalación manual (sin el script)

```powershell
git clone https://github.com/diego-lm/MeM.git
cd MeM
python -m venv .venv
.venv\Scripts\pip install -e . pytest
copy config.example.toml config.toml
```

## Primer arranque

1. Editar `config.toml` (nunca se sube a GitHub — queda solo en tu máquina):
   - `hamuq` — carpeta del vault (Markdown + frontmatter). Es una carpeta aparte del código, típicamente en Dropbox.
   - `proveedor` / `modelo` / `base_url` — qué IA responde el chat. Ver las tres opciones abajo.
2. Doble clic en `MeM.bat` (o dejar que el autostart lo haga en el próximo inicio de sesión).
3. Abrir `http://localhost:8765`.

### Proveedor de IA

Se elige en `config.toml` (o Ajustes → Proveedor de IA, ya con la app corriendo):

| `proveedor` | Qué usa | Requiere |
|---|---|---|
| `anthropic` | API de Claude | variable de entorno `ANTHROPIC_API_KEY` |
| `claude_code` | Claude Code instalado localmente | el comando `claude` y su suscripción (sin key) |
| `openai` | LM Studio u otro endpoint OpenAI-compatible | `base_url` (local o remoto) |

## Uso diario

El ícono de MeM vive en la bandeja del sistema (junto al reloj):

- **Clic izquierdo** — abre la app en el navegador.
- **Reiniciar** — mata y vuelve a levantar el servidor (útil tras `git pull` o cambiar `config.toml` a mano).
- **Salir** — apaga todo.

Si el ícono no está, doble clic en `MeM.bat` lo vuelve a levantar (un mutex evita tener dos corriendo a la vez).

### La app

- **Home** — captura rápida (texto, foto, audio) y accesos directos.
- **Memory** — explorar lo guardado: Recientes, Explorar (temas/proyectos/tags), Mapas (tiempo, lugares, grafo, mapa semántico), Media, Pendientes (inbox).
- **Sesiones** — conversaciones de trabajo sobre uno o más temas, con vista intercambiable:

  | Modo | Para qué |
  |---|---|
  | **chat** | conversación abierta sobre la memoria y la web |
  | **media** | crear imágenes, video y voz |
  | **mindmap** | nodos y relaciones entre temas y memorias |
  | **timeline** | los hechos de un tema en orden cronológico |

- **Ajustes** — proveedor de IA, apariencia (paleta/tipografía/radio/burbuja), procesamiento (reindexar búsqueda, backup), agentes, y **Media › Componentes** para instalar o sacar LM Studio / Ollama / ComfyUI.

### Comandos (`mem ...`)

Con el entorno activado (`.venv\Scripts\activate`) o llamando `.venv\Scripts\mem.exe` directo:

| Comando | Qué hace |
|---|---|
| `mem serve` | levanta API + UI web en `:8765` (lo mismo que hace el tray) |
| `mem ask "pregunta"` | pregunta única, sin guardar sesión |
| `mem chat --subjects "A,B"` | REPL de chat con sesión persistente |
| `mem capture "texto" --tags x --subjects Y` | nota cruda al inbox |
| `mem inbox` | pendientes + procesadas recientes |
| `mem process` | procesa el inbox: entiende, categoriza, etiqueta, indexa |
| `mem search "términos"` | busca en la memoria |
| `mem sessions [--modo]` | listar sesiones |
| `mem archive <id-sesion>` | destila la sesión a la Biblioteca y la archiva |
| `mem modes` | listar los modos de vista disponibles |
| `mem index` | reconstruye el índice de búsqueda (FTS5 + embeddings + geocache) |
| `mem lint [--semantico]` | chequeos de consistencia (links rotos, duplicados; `--semantico` suma un pase del LLM) |
| `mem reorganizar [--aplicar]` | re-evalúa los temas de toda la Biblioteca con el LLM (sin `--aplicar` solo muestra el plan) |
| `mem tags [--aplicar]` | unifica tags duplicados (mayúsculas, guiones, tildes) |
| `mem sintesis "Tema/Subtema"` | crea o regenera la página de síntesis de un tema |
| `mem ids` | one-shot: asigna id/versión a entradas viejas sin versionar |

## Conectar MeM a Claude

Resumen — instrucciones completas en [README.md § Conectar MeM a Claude](README.md#conectar-mem-a-claude-desktop-code-web-y-móvil).

- **Claude Desktop / Code** — vía `mem-plugin/` (stdio, `python -m mem.mcp`).
- **claude.ai (web y móvil)** — Settings → Connectors → Add custom connector → `https://<tu-host>/mcp/<mcp_secreto>`. Necesita `mcp_secreto` puesto en `config.toml` y esa ruta publicada (Tailscale Funnel u otro túnel HTTPS).

## Backup

Ajustes → Procesamiento → Backup: exporta el vault completo (memorias, tags, subjects fijados, adjuntos) a una carpeta a elección, e importa desde ahí en otra máquina. Es aparte de Dropbox — pensado para tener una copia portable/versionada de la memoria, no del código.

## Solución de problemas

**El puerto 8765 ya está en uso / dos íconos en la bandeja**
Un mutex global impide dos trays a la vez; si igual ves rareza, `Reiniciar` desde el menú del ícono, o matar el proceso `python.exe` que tiene el puerto y volver a abrir `MeM.bat`.

**El chat no responde / se cuelga sin error**
Los clientes de IA tienen 240s de timeout. Si el proveedor es `openai` (LM Studio), confirmar que el modelo esté cargado — la primera carga JIT tarda; si es `claude_code`, confirmar que `claude` tiene sesión iniciada (`claude` en una terminal).

**Falla la instalación del extra `[audio]`**
`faster-whisper` trae `ctranslate2`, que en Windows necesita cuBLAS/cuDNN aparte para GPU — sin eso corre en CPU (más lento pero funciona). Es opcional: sin él, una captura de audio queda pendiente con el motivo, nada se rompe.

**"No encuentro winget" con winget instalado**
El alias de winget vive en `%LOCALAPPDATA%\Microsoft\WindowsApps`, que no siempre está en el PATH del proceso. MeM lo busca igual por esa ruta, así que el aviso solo aparece si de verdad falta el "Instalador de aplicaciones" — se pone desde la Microsoft Store.

**Instalar un componente no termina nunca**
winget corre suelto y puede tardar varios minutos (son cientos de MB). El progreso real sale en el recuadro de log debajo del componente, en Ajustes › Media › Componentes; si winget está esperando un permiso de Windows, el diálogo está en la pantalla de la PC.

**Cambié algo y `install.ps1` no lo actualiza**
`install.ps1` corrido desde un checkout hace `git pull --ff-only`: si hay cambios locales sin commitear en ese checkout, va a fallar en vez de pisarlos. Resolver con `git status` ahí primero.

**Dónde está cada cosa**

| Qué | Dónde |
|---|---|
| Código de la app | este repo |
| Vault (memorias) | la carpeta que apunta `hamuq` en `config.toml` — fuera del repo |
| Configuración de esta máquina | `config.toml` (no se sube a git) |
| Índice de búsqueda | `<hamuq>/09_Sistema/_derivados/mem.db` — regenerable, `mem index` lo reconstruye |
| Logs del tray | `mem_tray.log` / `mem_tray.log.err`, en la raíz del repo |

---

*Este manual se actualiza junto con el código — ver `CLAUDE.md`.*
