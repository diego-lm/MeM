# Instalador unificado de MeM.
#
# Maquina limpia (instala lo que falte, clona y arma todo):
#   irm https://raw.githubusercontent.com/diego-lm/MeM/main/install.ps1 | iex
#
# Checkout ya clonado (actualiza en el lugar: dependencias, config, autostart):
#   .\install.ps1
#
# Componentes opcionales (LM Studio, Ollama, ComfyUI): sin -Con los pregunta uno
# por uno; con -Con instala esos y no pregunta nada. Desinstalar se hace desde la
# app (Ajustes > Media > Componentes), que usa el mismo componentes.json.
#   .\install.ps1 -Con lmstudio,comfyui
#   .\install.ps1 -SinPreguntar          # solo MeM, sin extras
#
# ------------------------------------------------------------------------------
# ESTE ARCHIVO ES ASCII PURO Y VA SIN BOM. No es capricho: son dos exigencias que
# se contradicen y solo el ASCII las satisface a la vez.
#   - Sin BOM, Windows PowerShell 5.1 lee un .ps1 con el codepage del sistema, y
#     un acento rompe el parser en lineas que ni lo tienen.
#   - Con BOM, `irm ... | iex` falla: Invoke-RestMethod entrega el BOM como
#     caracter literal U+FEFF y el parser se cae en el bloque param().
# Sin caracteres no-ASCII en el fuente, las dos vias andan. Los acentos que SI
# se ven en pantalla salen de componentes.json, que se lee en runtime como UTF-8.
# ------------------------------------------------------------------------------
param(
    [string]$Destino = "$env:USERPROFILE\MeM",
    [string[]]$Con = @(),   # ids de componentes.json a instalar sin preguntar
    [switch]$SinPreguntar,  # nunca preguntar (desatendido): no instala extras
    [switch]$Audio,         # instala tambien el extra [audio] (faster-whisper, pesado)
    [switch]$AudioGpu,      # como -Audio pero con las DLL de CUDA: ~15x mas rapido, ~700 MB
    [switch]$Video,         # extra [video] (yt-dlp) + ffmpeg: links de YouTube y videos largos
    [switch]$SinAutostart   # no crear el acceso directo de arranque automatico
)

$ErrorActionPreference = "Stop"
$repo = "diego-lm/MeM"

# Los textos de componentes.json llevan acentos; sin esto la consola los escupe
# segun su codepage (se vio "DespuA(c)s" en una corrida real).
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Fallar($msg) { Write-Host "`n[X] $msg" -ForegroundColor Red; exit 1 }
function Paso($msg) { Write-Host "`n> $msg" -ForegroundColor Cyan }
function Aviso($msg) { Write-Host "  $msg" -ForegroundColor Yellow }

# Read-Host revienta (o lee EOF) cuando no hay nadie del otro lado: en desatendido
# la respuesta es "no", que es la que no instala nada por sorpresa.
function Preguntar($msg) {
    if ($SinPreguntar) { return $false }
    try { $r = Read-Host "  $msg [s/N]" } catch { return $false }
    return $r -match '^\s*(s|si|y|yes)\s*$'
}

# El alias de winget vive en %LOCALAPPDATA%\Microsoft\WindowsApps, que no siempre
# esta en el PATH del proceso (visto en esta maquina: winget andando y
# `Get-Command winget` vacio). Mismo fallback que mem/componentes.py.
function Buscar-Winget {
    $cmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $alias = "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe"
    if (Test-Path $alias) { return $alias }
    return $null
}

# winget install deja el .exe nuevo en el PATH del SISTEMA, no en el de esta
# sesion: sin releerlo, el git que acabamos de instalar "no existe" dos lineas
# mas abajo.
function Refrescar-Path {
    $env:PATH = ([Environment]::GetEnvironmentVariable("Path", "Machine"),
                 [Environment]::GetEnvironmentVariable("Path", "User")) -join ';'
}

function Winget-Instalar($id, $nombre) {
    $w = Buscar-Winget
    if (-not $w) {
        Fallar "falta $nombre y no encuentro winget para instalarlo. Instala 'Instalador de aplicaciones' desde la Microsoft Store, o pone $nombre a mano."
    }
    Paso "Instalando $nombre (winget: $id)"
    & $w install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Fallar "winget no pudo instalar $nombre (codigo $LASTEXITCODE)." }
    Refrescar-Path
}

# Si el script corre desde un checkout existente (doble clic, .\install.ps1) y
# no se paso -Destino a mano, actualiza ESE lugar en vez de clonar uno nuevo.
# Con `irm | iex` no hay archivo y $PSScriptRoot viene vacio: clona, que es lo
# que corresponde en una maquina limpia.
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

# ponytail: los dos ids de aca son los unicos hardcodeados. No pueden salir de
# componentes.json porque se necesitan ANTES de que exista el repo que lo trae.
Paso "Buscando Python 3.11+"
function Buscar-Python {
    # el primer `python` del PATH puede ser el venv de otro proyecto (visto en
    # esta maquina: uno de Hermes ganaba al Python real), asi que se descartan
    # los venv y se pregunta la version en vez de confiar en el orden.
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
    if (-not $python) { Fallar "instale Python pero no lo encuentro. Cerra y abri la terminal, y volve a correr esto." }
}
Write-Host "  $python"

$gh = Get-Command gh -ErrorAction SilentlyContinue

if (-not $yaClonado) {
    Paso "Clonando en $Destino"
    if (Test-Path $Destino) { Fallar "$Destino ya existe y no es un checkout de MeM. Borralo o elegi otro -Destino." }
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
    $extras = @()
    if ($Audio)    { $extras += "audio" }
    if ($AudioGpu) { $extras += "audio-gpu" }
    if ($Video)    { $extras += "video" }
    $paquete = if ($extras.Count) { ".[" + ($extras -join ",") + "]" } else { "." }
    & .venv\Scripts\pip install -q -e $paquete pytest
    if ($LASTEXITCODE -ne 0) { Fallar "pip install fallo (ver arriba)." }

    if (-not (Test-Path "config.toml")) {
        Copy-Item "config.example.toml" "config.toml"
        Aviso "creado config.toml - falta editar 'hamuq' (carpeta del vault) y el proveedor de IA."
    }
} finally {
    Pop-Location
}

# --- componentes opcionales -------------------------------------------------
# Que hay puesto lo dice mem.componentes, no un `winget list` propio: es el mismo
# modulo que usa la app, y sabe ademas reconocer una instalacion hecha a mano
# (ComfyUI clonado a pulso no es "falta ComfyUI", y ofrecer el paquete de winget
# ahi dejaria DOS ComfyUI en la maquina).
# yt-dlp junta audio y video con ffmpeg, y leer un video tambien lo necesita:
# pedir -Video sin ffmpeg deja la mitad puesta.
if ($Video -and $Con -notcontains "ffmpeg") { $Con += "ffmpeg" }

Paso "Componentes opcionales"
Push-Location $Destino
try {
    $leer = 'import json; from mem import componentes, config; print(json.dumps(componentes.estado(config.cargar())))'
    $estado = (& .venv\Scripts\python.exe -c $leer 2>$null | ConvertFrom-Json)
} catch { $estado = $null }
finally { Pop-Location }

if (-not $estado) {
    Aviso "no pude leer el catalogo de componentes - se instalan igual desde Ajustes > Media > Componentes."
} else {
    $ids = $estado.componentes.PSObject.Properties
    $desconocidos = $Con | Where-Object { $_ -notin $ids.Name }
    if ($desconocidos) { Fallar "componente(s) que no existen: $($desconocidos -join ', '). Hay: $($ids.Name -join ', ')" }
    if (-not $estado.winget) { Aviso "sin winget no puedo instalar componentes (falta 'Instalador de aplicaciones')." }

    foreach ($p in $ids) {
        $c = $p.Value
        if ($c.instalacion -eq "winget") { Write-Host "  [ok] $($c.nombre) ya esta"; continue }
        if ($c.instalacion -eq "manual") { Write-Host "  [ok] $($c.nombre) ya esta (instalado a mano)"; continue }
        if (-not $estado.winget) { continue }
        $quiere = if ($Con.Count) { $p.Name -in $Con }
                  elseif ($SinPreguntar) { $false }
                  else {
                      Write-Host "  $($c.nombre) - $($c.que_es.es)"
                      Preguntar "Instalar $($c.nombre)?"
                  }
        if ($quiere) { Winget-Instalar $c.winget $c.nombre }
    }
    Write-Host "  (se instalan y se sacan despues desde Ajustes > Media > Componentes)"
}

if (-not $SinAutostart) {
    Paso "Arranque automatico al iniciar sesion"
    $lnkPath = Join-Path ([Environment]::GetFolderPath("Startup")) "MeM.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = Join-Path $Destino "MeM.bat"
    $lnk.WorkingDirectory = $Destino
    $lnk.WindowStyle = 7  # minimizada
    $lnk.Save()
    Write-Host "  $lnkPath"
}

Write-Host "`n[ok] Listo." -ForegroundColor Green
Write-Host "  Arrancar ahora:  $Destino\MeM.bat"
Write-Host "  Despues:         http://localhost:8765"
