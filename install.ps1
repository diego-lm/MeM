# Instalador unificado de MeM.
#
# Máquina nueva (clona y arma todo):
#   irm https://raw.githubusercontent.com/diego-lm/MeM/main/install.ps1 | iex
#
# Checkout ya clonado (actualiza en el lugar — dependencias, config, autostart):
#   .\install.ps1
#
param(
    [string]$Destino = "$env:USERPROFILE\MeM",
    [switch]$Audio,        # instala también el extra [audio] (faster-whisper, pesado)
    [switch]$SinAutostart  # no crear el acceso directo de arranque automático
)

$ErrorActionPreference = "Stop"
$repo = "diego-lm/MeM"

function Fallar($msg) { Write-Host "`n✗ $msg" -ForegroundColor Red; exit 1 }
function Paso($msg) { Write-Host "`n→ $msg" -ForegroundColor Cyan }

# Si el script corre desde un checkout existente (doble clic, .\install.ps1),
# actualiza ESE lugar en vez de clonar uno nuevo en $Destino.
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) {
    $Destino = $PSScriptRoot
}
$yaClonado = Test-Path (Join-Path $Destino ".git")

Paso "Buscando Python 3.11+"
$candidatos = Get-Command python -All -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notmatch '\\venv\\|\\\.venv\\' } |
    Select-Object -ExpandProperty Source -Unique
$python = $null
foreach ($c in $candidatos) {
    if ((& $c -c "import sys; print(sys.version_info>=(3,11))" 2>$null) -eq "True") { $python = $c; break }
}
if (-not $python) { Fallar "No se encontró Python 3.11+. Instalalo con:`n    winget install Python.Python.3.12`ny volvé a correr este script." }
Write-Host "  $python"

Paso "Buscando git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fallar "No se encontró git. Instalalo con:`n    winget install Git.Git" }
$gh = Get-Command gh -ErrorAction SilentlyContinue

if (-not $yaClonado) {
    Paso "Clonando en $Destino"
    if (Test-Path $Destino) { Fallar "$Destino ya existe y no es un checkout de MeM. Borralo o elegí otro -Destino." }
    if ($gh) { & $gh repo clone $repo $Destino } else { git clone "https://github.com/$repo.git" $Destino }
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
        Write-Host "  creado config.toml — falta editar 'hamuq' (carpeta del vault) y el proveedor de IA." -ForegroundColor Yellow
    }
} finally {
    Pop-Location
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
