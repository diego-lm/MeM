# Manual de uso — MeM

Guía práctica para instalar, arrancar y usar MeM día a día. Para arquitectura interna, formato del vault y diseño del frontend, ver [README.md](README.md) y [FORMATO_MEMORIA.md](FORMATO_MEMORIA.md).

## Qué es

MeM es tu memoria personal: una base de Markdown (`hamuQ/`, en Dropbox) que capturás desde el celu o la compu, la app organiza sola (tags, temas, conexiones, búsqueda), y con la que podés chatear en **Sesiones** de trabajo. Corre local en Windows: un servidor (FastAPI) + una app web (PWA) en `http://localhost:8765`, con un ícono en la bandeja del sistema.

## Instalación

### Máquina nueva

**Opción 1 — el ejecutable.** Bajar `MeM-Instalador.exe` del [último Release](https://github.com/diego-lm/MeM/releases/latest) y hacerle doble clic. No hace falta tener nada instalado ni cuenta de GitHub.

Windows va a mostrar **"Windows protegió tu PC"** (SmartScreen), porque el `.exe` no está firmado — firmar cuesta unos USD 200-400 por año. Para seguir: *Más información* → *Ejecutar de todas formas*. Si preferís no pasar por ahí, usá la opción 2, que hace exactamente lo mismo.

**Opción 2 — una línea en PowerShell.** Sin SmartScreen de por medio:

```powershell
irm https://raw.githubusercontent.com/diego-lm/MeM/main/install.ps1 | iex
```

Las dos hacen lo mismo (el `.exe` corre esa misma línea): instalan lo que falte (git, Python), clonan el repo en `%USERPROFILE%\MeM`, crean el entorno virtual, instalan dependencias, copian `config.example.toml` → `config.toml`, ofrecen los componentes opcionales y dejan un acceso directo para que MeM arranque solo al iniciar sesión. Lo que ya esté instalado se detecta y se saltea.

Si preferís ver el script antes de correrlo (buena costumbre con cualquier `| iex`), bajalo primero y hacele doble clic a `Instalar-MeM.bat` después de clonar:

```powershell
git clone https://github.com/diego-lm/MeM.git "$env:USERPROFILE\MeM"
```

Lo único que tiene que estar de antes es **winget** (el "Instalador de aplicaciones" de Windows), que viene con Windows 10/11; si falta, se pone desde la Microsoft Store.

Parámetros (para la opción 2 o para `install.ps1` desde un checkout; el `.exe` corre siempre con los valores por omisión):
- `-Con <ids>` — instalar estos componentes sin preguntar, p. ej. `-Con lmstudio,comfyui`.
- `-SinPreguntar` — desatendido: solo MeM, ningún componente opcional.
- `-Destino <carpeta>` — instalar en otro lado (default `%USERPROFILE%\MeM`).
- `-Audio` — instala también faster-whisper (transcripción de audio local; pesado, ver [Solución de problemas](#solución-de-problemas)).
- `-AudioGpu` — igual que `-Audio` pero con las DLL de CUDA: transcribe en la placa de video, varias veces más rápido, a cambio de ~700 MB más. Es lo que hace viable un video de una hora.
- `-Video` — links de YouTube y videos largos: instala `yt-dlp` y agrega FFmpeg a los componentes.
- `-SinAutostart` — no crear el acceso directo de arranque automático.

### Componentes opcionales

Ni MeM ni el instalador traen los motores de IA: los instala **winget**, el gestor de paquetes de Windows, y por eso actualizarlos o sacarlos después funciona como con cualquier otro programa.

| Componente | Para qué | Paquete |
|---|---|---|
| **LM Studio** | modelos de lenguaje locales, servidor OpenAI-compatible en `localhost:1234` | `ElementLabs.LMStudio` |
| **Ollama** | la otra vía de modelos locales, por línea de comandos; sirve en `localhost:11434/v1` | `Ollama.Ollama` |
| **FFmpeg** | leer un video: sacarle los fotogramas y la pista de audio (hace falta para videos y para YouTube) | `Gyan.FFmpeg` |
| **ComfyUI** | generación local de imagen y video (backend `local` del modo media) | `Comfy.ComfyUI-Desktop` |

El instalador **solo ofrece lo que falta**: mira el estado de cada uno y el que ya está lo marca `[ok]` y lo saltea, así que no hay riesgo de terminar con dos ComfyUI. De los que faltan pregunta uno por uno (`Instalar Ollama? [s/N]`), y con `-Con lmstudio,comfyui` los instala sin preguntar nada. Después se ponen y se sacan desde **Ajustes › Media › Componentes**, sin volver a la terminal. Windows puede pedir permiso (UAC) en la PC mientras winget trabaja, aunque la orden haya salido del celular.

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

Si el código en disco quedó más nuevo que el servidor corriendo (por ejemplo tras un `git pull` sin reiniciar), el ícono se marca con un punto rojo. Se revisa cada 5 minutos; **Reiniciar** lo saca.

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

- **Ajustes** — proveedor de IA, apariencia (paleta/tipografía/radio/burbuja), procesamiento (reindexar búsqueda, backup), agentes, y **Media › Componentes** para instalar o sacar LM Studio / Ollama / ComfyUI. Se llega también con el ícono `⚙` de Home, al lado del de tema claro/oscuro.

Home muestra la versión (`vN`) al lado de ese mismo ícono. Si el servidor corriendo quedó atrás del código en disco, en vez del número aparece un botón de actualizar (el mismo reinicio remoto de Ajustes › Procesamiento).

### Proyectos y privado

El selector de proyecto (chip del costado en compu, última pestaña en el celular) tiene tres zonas: **Todo** (la biblioteca compartida: lo público de todos los proyectos), la lista de tus proyectos (los privados marcados con `⚿`), y **✎ Gestionar** para crear, renombrar, unir, borrar o marcar un proyecto como privado.

Un proyecto ordena; uno **privado** además encierra: todo lo suyo (sesiones, memorias, capturas, medios) solo se ve parado en él. Marcar un proyecto como privado o público cambia la visibilidad de todo lo suyo al instante — no hay nada que migrar a mano. En el celular, lo privado queda tapado hasta tocar "Ver privado" y verificar con huella/cara/PIN; en compu se ve siempre, marcado en rojo.

Parado en un proyecto, la pantalla **Memory** solo lista lo de ese proyecto (no lo público de los demás mezclado); parado en **Todo**, lista lo público de todos.

Una memoria o sesión vive en un solo proyecto. Para "compartir" algo que está en un proyecto privado, se la mueve a **Todo**: tocar el chip **Proyecto: …** de la ficha (o el de la sesión) abre "Mover a…", el mismo mecanismo en los dos casos.

En una sesión de chat, **Guardar en memoria** (menú `⋯`, o el aviso cuando el contexto se llena) destila lo hablado a la Biblioteca sin tocar la sesión; una casilla opcional "y archivar la sesión" además la archiva. Lo destilado hereda el proyecto de la sesión — y su privacidad — sin ninguna marca aparte.

Desde **✎ Gestionar**, un proyecto privado puede generar una **clave de acceso** (botón "Clave de acceso (MCP/CLI)"): la única forma de que Claude Desktop/Code o el CLI lean ese proyecto (ver [Conectar MeM a Claude](#conectar-mem-a-claude) más abajo). Se muestra una sola vez al generarla.

### Comandos (`mem ...`)

Con el entorno activado (`.venv\Scripts\activate`) o llamando `.venv\Scripts\mem.exe` directo:

| Comando | Qué hace |
|---|---|
| `mem serve` | levanta API + UI web en `:8765` (lo mismo que hace el tray) |
| `mem ask "pregunta" [--proyecto P] [--clave K]` | pregunta única, sin guardar sesión |
| `mem chat --subjects "A,B" [--proyecto P] [--clave K]` | REPL de chat con sesión persistente |
| `mem capture "texto" --tags x --subjects Y [--proyecto P]` | nota cruda al inbox |
| `mem inbox` | pendientes + procesadas recientes |
| `mem process` | procesa el inbox: entiende, categoriza, etiqueta, indexa |
| `mem search "términos" [--proyecto P] [--clave K]` | busca en la memoria |
| `mem sessions [--modo]` | listar sesiones |
| `mem archive <id-sesion>` | destila la sesión a la Biblioteca y la archiva |
| `mem modes` | listar los modos de vista disponibles |
| `mem index` | reconstruye el índice de búsqueda (FTS5 + embeddings + geocache) |
| `mem lint [--semantico]` | chequeos de consistencia (links rotos, duplicados; `--semantico` suma un pase del LLM) |
| `mem reorganizar [--aplicar]` | re-evalúa los temas de toda la Biblioteca con el LLM (sin `--aplicar` solo muestra el plan) |
| `mem tags [--aplicar]` | unifica tags duplicados (mayúsculas, guiones, tildes) |
| `mem sintesis "Tema/Subtema"` | crea o regenera la página de síntesis de un tema |
| `mem ids` | one-shot: asigna id/versión a entradas viejas sin versionar |

Sin `--proyecto`, estos comandos solo ven **Todo** (lo público). `--proyecto P` suma lo de ese proyecto; si es privado, hace falta además `--clave K` (la que generás en Ajustes › ✎ Gestionar › Clave de acceso (MCP/CLI)).

## Conectar MeM a Claude

Resumen — instrucciones completas en [README.md § Conectar MeM a Claude](README.md#conectar-mem-a-claude-desktop-code-web-y-móvil).

- **Claude Desktop / Code** — vía `mem-plugin/` (stdio, `python -m mem.mcp`).
- **claude.ai (web y móvil)** — Settings → Connectors → Add custom connector → `https://<tu-host>/mcp/<mcp_secreto>`. Necesita `mcp_secreto` puesto en `config.toml` y esa ruta publicada (Tailscale Funnel u otro túnel HTTPS).

Sin nada más, Claude solo ve **Todo** (lo público). Para que también lea un proyecto privado: Desktop/Code toman las claves de la variable de entorno `MEM_CLAVES` (una vez, no hay que pasarla en cada pregunta — ver README); claude.ai la pide en cada llamada a la herramienta, porque no hay entorno de por medio.

## Backup

Ajustes → Procesamiento → Backup: exporta el vault completo (memorias, tags, subjects fijados, adjuntos) a una carpeta a elección, e importa desde ahí en otra máquina. Es aparte de Dropbox — pensado para tener una copia portable/versionada de la memoria, no del código.

## Solución de problemas

**El puerto 8765 ya está en uso / dos íconos en la bandeja**
Un mutex global impide dos trays a la vez; si igual ves rareza, `Reiniciar` desde el menú del ícono, o matar el proceso `python.exe` que tiene el puerto y volver a abrir `MeM.bat`.

**El ícono está en la bandeja pero MeM no abre (local ni por Tailscale, que da 502)**
Mirar `mem_tray.log.err`: si dice `WinError 10013` al bindear el 8765, Windows tenía el puerto reservado para Hyper-V/WinNAT al arrancar (pasa cuando el rango dinámico de puertos empieza en 1024, `netsh int ipv4 show dynamicport tcp`). El tray reintenta solo durante 5 minutos; si sigue caído, `Reiniciar` desde el menú del ícono. Para que no vuelva a pasar, reservar el puerto una vez desde una terminal **como administrador**:

```
netsh int ipv4 add excludedportrange protocol=tcp startport=8765 numberofports=1
```

Si netsh responde "el proceso no puede acceder al archivo porque está en uso", es que algo escucha en el 8765: parar MeM (`Salir` en el ícono) y, si usás `tailscale serve` en ese puerto, apagarlo con `tailscale serve --https=8765 off`; correr el netsh y volver a activarlo con `tailscale serve --bg --https=8765 http://127.0.0.1:8765`.

**El chat no responde / se cuelga sin error**
Los clientes de IA tienen 240s de timeout. Si el proveedor es `openai` (LM Studio), confirmar que el modelo esté cargado — la primera carga JIT tarda; si es `claude_code`, confirmar que `claude` tiene sesión iniciada (`claude` en una terminal).

**Falla la instalación del extra `[audio]`**
`faster-whisper` trae `ctranslate2`, que en Windows necesita cuBLAS/cuDNN aparte para GPU — sin eso corre en CPU (más lento pero funciona). Es opcional: sin él, una captura de audio queda pendiente con el motivo, nada se rompe.

**Un video tarda muchísimo en procesarse**
En CPU la transcripción va a ~1x tiempo real: un video de una hora tarda una hora. Se procesa en segundo plano y el avance sale en `log.md` del vault (líneas `media`), así que MeM sigue usable mientras tanto. Para que vaya varias veces más rápido, reinstalar con `-AudioGpu` (son las DLL de CUDA que faltaban). Los videos de YouTube que traen subtítulos ni transcriben: se usan los subtítulos.

**Un link de YouTube no se procesa**
Hace falta `yt-dlp` (instalar con `-Video`) y FFmpeg. MeM solo lee videos **públicos**: si el video pide cuenta, la memoria se crea igual y queda marcada con el motivo. El video se baja a 360p a una carpeta temporal, se mira y se borra — en la memoria queda la línea de tiempo y el link, nunca el archivo.

**El mapa no geocodifica lugares**
El mapa usa Nominatim (OpenStreetMap), que limita a 1 consulta por segundo y pide que el User-Agent identifique la app. MeM ya lo hace; si vas a geocodificar mucho, su política recomienda dar un contacto: `setx MEM_CONTACTO tu@mail.com` y reiniciar el servidor. No es obligatorio para uso normal.

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
