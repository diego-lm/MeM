# Instalador unificado de MeM.
#
# Máquina limpia (instala lo que falte, clona y arma todo):
#   irm https://raw.githubusercontent.com/diego-lm/MeM/main/install.ps1 | iex
#
# Checkout ya clonado (actualiza en el lugar — dependencias, config, autostart):
#   .\install.ps1
#
# Componentes opcionales (LM Studio, Ollama, ComfyUI): sin -Con los pregunta uno
# por uno; con -Con instala esos y no pregunta nada. Desinstalar se hace desde la
# app (Ajustes › Media › Componentes), que usa el mismo componentes.json.
#   .\install.ps1 -Con lmstudio,comfyui
#   .\install.ps1 -SinPreguntar          # solo MeM, sin extras
#
param(
    [string]$Destino = "$env:USERPROFILE\MeM",
    [string[]]$Con = @(),   # ids de componentes.json a instalar sin preguntar
    [switch]$SinPreguntar,  # nunca preguntar (desatendido): no instala extras
    [switch]$Audio,         # instala también el extra [audio] (faster-whisper, pesado)
    [switch]$SinAutostart   # no crear el acceso directo de arranque automático
)

$ErrorActionPreference = "Stop"
$repo = "diego-lm/MeM"

function Fallar($msg) { Write-Host "`n✗ $msg" -ForegroundColor Red; exit 1 }
function Paso($msg) { Write-Host "`n→ $msg" -ForegroundColor Cyan }
function Aviso($msg) { Write-Host "  $msg" -ForegroundColor Yellow }

# Read-Host revienta (o lee EOF) cuando no hay nadie del otro lado: en desatendido
# la respuesta es "no", que es la que no instala nada por sorpresa.
function Preguntar($msg) {
    if ($SinPreguntar) { return $false }
    try { $r = Read-Host "  $msg [s/N]" } catch { return $false }
    return $r -match '^\s*(s|si|sí|y|yes)\s*$'
}

# El alias de winget vive en %LOCALAPPDATA%\Microsoft\WindowsApps, que no siempre
# está en el PATH del proceso (visto en esta máquina: winget andando y
# `Get-Command winget` vacío). Mismo fallback que mem/componentes.py.
function Buscar-Winget {
    $cmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $alias = "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe"
    if (Test-Path $alias) { return $alias }
    return $null
}

# winget install deja el .exe nuevo en el PATH del SISTEMA, no en el de esta
# sesión: sin releerlo, el git que acabamos de instalar "no existe" dos líneas
# más abajo.
function Refrescar-Path {
    $env:PATH = ([Environment]::GetEnvironmentVariable("Path", "Machine"),
                 [Environment]::GetEnvironmentVariable("Path", "User")) -join ';'
}

function Winget-Instalar($id, $nombre) {
    $w = Buscar-Winget
    if (-not $w) {
        Fallar "falta $nombre y no encuentro winget para instalarlo. Instalá 'Instalador de aplicaciones' desde la Microsoft Store, o poné $nombre a mano."
    }
    Paso "Instalando $nombre (winget: $id)"
    & $w install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Fallar "winget no pudo instalar $nombre (código $LASTEXITCODE)." }
    Refrescar-Path
}

# Si el script corre desde un checkout existente (doble clic, .\install.ps1) y
# no se pasó -Destino a mano, actualiza ESE lugar en vez de clonar uno nuevo.
if (-not $PSBoundParameters.ContainsKey('Destino') -and $PSScriptRoot -and
    (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) {
    $Destino = $PSScriptRoot
}
$yaClonado = Test-Path (Join-Path $Destino ".git")

# --- base: git y Python. Son requisitos, no opciones: sin ellos no hay MeM. ---
Paso "Buscando git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Refrescar-Path
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Winget-Instalar "Git.Git" "git" }
}
Write-Host "  $((Get-Command git).Source)"

# ponytail: los dos ids de acá son los únicos hardcodeados. No pueden salir de
# componentes.json porque se necesitan ANTES de que exista el repo que lo trae.
Paso "Buscando Python 3.11+"
function Buscar-Python {
    # el primer `python` del PATH puede ser el venv de otro proyecto (visto en
    # esta máquina: uno de Hermes ganaba al Python real), así que se descartan
    # los venv y se pregunta la versión en vez de confiar en el orden.
    $cands = Get-Command python -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\venv\\|\\\.venv\\' } |
        Select-Object -ExpandProperty Source -Unique
    foreach ($c in $cands) {
        if ((& $c -c "import sys; print(sys.version_info>=(3,11))" 2>$null) -eq "True") { return $c }
    }
    return $null
}
$python = Buscar-Python
if (-not $python) {
    Winget-Instalar "Python.Python.3.12" "Python 3.12"
    $python = Buscar-Python
    if (-not $python) { Fallar "instalé Python pero no lo encuentro. Cerrá y abrí la terminal, y volvé a correr esto." }
}
Write-Host "  $python"

$gh = Get-Command gh -ErrorAction SilentlyContinue

if (-not $yaClonado) {
    Paso "Clonando en $Destino"
    if (Test-Path $Destino) { Fallar "$Destino ya existe y no es un checkout de MeM. Borralo o elegí otro -Destino." }
    if ($gh) { & $gh repo clone $repo $Destino } else { git clone "https://github.com/$repo.git" $Destino }
    if ($LASTEXITCODE -ne 0) { Fallar "no pude clonar $repo." }
} else {
    Paso "Actualizando checkout en $Destino"
    git -C $Destino pull --ff-only
}

Paso "Instalando dependencias (venv + pip)"
Push-Location $Destino
try {
    if (-not (Test-Path ".venv")) { & $python -m venv .venv }
    $paquete = if ($Audio) { ".[audio]" } else { "." }
    & .venv\Scripts\pip install -q -e $paquete pytest
    if ($LASTEXITCODE -ne 0) { Fallar "pip install falló (ver arriba)." }

    if (-not (Test-Path "config.toml")) {
        Copy-Item "config.example.toml" "config.toml"
        Aviso "creado config.toml — falta editar 'hamuq' (carpeta del vault) y el proveedor de IA."
    }
} finally {
    Pop-Location
}

# --- componentes opcionales -------------------------------------------------
# Qué hay puesto lo dice mem.componentes, no un `winget list` propio: es el mismo
# módulo que usa la app, y sabe además reconocer una instalación hecha a mano
# (ComfyUI clonado a pulso no es "falta ComfyUI", y ofrecer el paquete de winget
# ahí dejaría DOS ComfyUI en la máquina).
Paso "Componentes opcionales"
Push-Location $Destino
try {
    $leer = 'import json; from mem import componentes, config; print(json.dumps(componentes.estado(config.cargar())))'
    $estado = (& .venv\Scripts\python.exe -c $leer 2>$null | ConvertFrom-Json)
} catch { $estado = $null }
finally { Pop-Location }

if (-not $estado) {
    Aviso "no pude leer el catálogo de componentes — se instalan igual desde Ajustes › Media › Componentes."
} else {
    $ids = $estado.componentes.PSObject.Properties
    $desconocidos = $Con | Where-Object { $_ -notin $ids.Name }
    if ($desconocidos) { Fallar "componente(s) que no existen: $($desconocidos -join ', '). Hay: $($ids.Name -join ', ')" }
    if (-not $estado.winget) { Aviso "sin winget no puedo instalar componentes (falta 'Instalador de aplicaciones')." }

    foreach ($p in $ids) {
        $c = $p.Value
        if ($c.instalacion -eq "winget") { Write-Host "  ✓ $($c.nombre) ya está"; continue }
        if ($c.instalacion -eq "manual") { Write-Host "  ✓ $($c.nombre) ya está (instalado a mano)"; continue }
        if (-not $estado.winget) { continue }
        $quiere = if ($Con.Count) { $p.Name -in $Con }
                  elseif ($SinPreguntar) { $false }
                  else {
                      Write-Host "  $($c.nombre) — $($c.que_es.es)"
                      Preguntar "¿Instalar $($c.nombre)?"
                  }
        if ($quiere) { Winget-Instalar $c.winget $c.nombre }
    }
    Write-Host "  (se instalan y se sacan después desde Ajustes › Media › Componentes)"
}

if (-not $SinAutostart) {
    Paso "Arranque automático al iniciar sesión"
    $lnkPath = Join-Path ([Environment]::GetFolderPath("Startup")) "MeM.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = Join-Path $Destino "MeM.bat"
    $lnk.WorkingDirectory = $Destino
    $lnk.WindowStyle = 7  # minimizada
    $lnk.Save()
    Write-Host "  $lnkPath"
}

Write-Host "`n✓ Listo." -ForegroundColor Green
Write-Host "  Arrancar ahora:  $Destino\MeM.bat"
Write-Host "  Después:         http://localhost:8765"
