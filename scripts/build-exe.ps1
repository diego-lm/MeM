# Arma MeM-Instalador.exe: el doble clic para instalar MeM en una maquina limpia.
#
#   .\scripts\build-exe.ps1              -> MeM-Instalador.exe en la raiz del repo
#   .\scripts\build-exe.ps1 -Salida X    -> en otro lado
#
# Lo compila csc.exe, el compilador de C# que VIENE CON WINDOWS (.NET Framework
# 4.x, en %WINDIR%\Microsoft.NET\Framework64\v4.0.30319). Sin ps2exe, sin
# PSGallery, sin SDK: el .exe se arma en cualquier Windows.
#
# ponytail: el .exe NO trae install.ps1 adentro, solo lanza el one-liner que lo
# baja. Asi se arma UNA vez y no queda viejo: cada cambio de install.ps1 le llega
# solo al que ya bajo el .exe, en vez de haber que recompilarlo y volver a
# subirlo al Release en cada cambio.
#
# Se probo antes con iexpress.exe (tambien nativo, hace un self-extractor .exe):
# arma el paquete sin chistar y con exit 0, pero en Windows 11 26200 NO ejecuta
# su AppLaunched -- comprobado con un payload que solo escribia un archivo, y no
# lo escribio. No volver por ahi.
#
# Este archivo es ASCII puro por la misma razon que install.ps1 (ver CLAUDE.md);
# el .cs generado se escribe como UTF-8 con BOM, que es lo que csc espera.
param(
    [string]$Salida = (Join-Path (Split-Path $PSScriptRoot -Parent) "MeM-Instalador.exe")
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path $PSScriptRoot -Parent
$url = "https://raw.githubusercontent.com/diego-lm/MeM/main/install.ps1"

$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path $csc)) { throw "no encuentro csc.exe (.NET Framework 4.x viene con Windows)." }

# El .exe es una app de CONSOLA a proposito: hereda la ventana al hijo, y por eso
# install.ps1 puede preguntar "instalar Ollama? [s/N]" y leer la respuesta. Con
# WinExe no habria consola y las preguntas se perderian.
$cs = @"
using System;
using System.Diagnostics;

class Instalador {
    static int Main() {
        Console.Title = "Instalador de MeM";
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        Console.WriteLine("Instalador de MeM");
        Console.WriteLine();
        Console.WriteLine("Bajando el instalador y arrancando...");
        Console.WriteLine();

        var psi = new ProcessStartInfo("powershell.exe",
            "-NoProfile -ExecutionPolicy Bypass -Command \"irm $url | iex\"");
        psi.UseShellExecute = false;   // hereda esta consola: las preguntas funcionan

        int codigo;
        try {
            var p = Process.Start(psi);
            p.WaitForExit();
            codigo = p.ExitCode;
        } catch (Exception e) {
            Console.WriteLine("No pude arrancar PowerShell: " + e.Message);
            codigo = 1;
        }

        Console.WriteLine();
        Console.Write("Presiona Enter para cerrar...");
        Console.ReadLine();
        return codigo;
    }
}
"@

$tmp = Join-Path $env:TEMP "mem-exe.cs"
[IO.File]::WriteAllText($tmp, $cs, (New-Object Text.UTF8Encoding($true)))

$icono = Join-Path $raiz "mem\static\assets\favicon.ico"
$args = @("/nologo", "/target:exe", "/optimize+", "/out:$([IO.Path]::GetFullPath($Salida))")
if (Test-Path $icono) { $args += "/win32icon:$icono" }
$args += $tmp

& $csc @args
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { throw "csc fallo (codigo $LASTEXITCODE)." }

"{0}  ({1:N0} KB)" -f [IO.Path]::GetFullPath($Salida), ((Get-Item $Salida).Length / 1KB)
