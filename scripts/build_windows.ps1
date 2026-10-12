[CmdletBinding()]
param([switch]$Launch)
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Program $($Arguments -join ' ')"
    }
}

if ($env:OS -ne 'Windows_NT') { throw 'Build on Windows 10/11 x64.' }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if (!(Test-Path 'pyproject.toml') -or !(Test-Path 'RS02Configurator.spec')) {
        throw 'Extract the complete repository before running this script.'
    }
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (!$PyLauncher) {
        throw 'Install Python 3.11 x64 with the Python launcher: winget install --exact --id Python.Python.3.11'
    }
    $PythonPath = & $PyLauncher.Source -3.11 -c 'import sys,struct; assert sys.version_info[:2] == (3,11) and struct.calcsize(''P'') == 8, ''Python 3.11 x64 required''; print(sys.executable)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 x64 is required. Install it and retry.' }
    $PythonPath = ($PythonPath | Select-Object -Last 1).Trim()
    $ToolsPython = Join-Path $ProjectRoot '.build-tools\Scripts\python.exe'
    if (!(Test-Path $ToolsPython)) {
        Invoke-Checked $PythonPath @('-m', 'venv', '.build-tools')
    }
    # Local bootstrap only: no global PATH, driver, or execution-policy changes.
    Invoke-Checked $ToolsPython @('-m', 'pip', 'install', 'uv>=0.9,<1')
    $Uv = Join-Path $ProjectRoot '.build-tools\Scripts\uv.exe'
    Invoke-Checked $Uv @('sync', '--frozen', '--extra', 'dev', '--python', $PythonPath)
    $AppPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    Invoke-Checked $AppPython @('-c', 'import libusb_package,ctypes; p=libusb_package.get_library_path(); assert p and p.suffix == ''.dll'', ''Bundled libusb DLL missing''; ctypes.CDLL(str(p)); print(''libusb DLL loaded:'', p)')
    $PreviousQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = 'offscreen'
        Invoke-Checked $AppPython @('-m', 'pytest', '-q')
    } finally {
        if ($null -eq $PreviousQtPlatform) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
        else { $env:QT_QPA_PLATFORM = $PreviousQtPlatform }
    }
    Invoke-Checked (Join-Path $ProjectRoot '.venv\Scripts\ruff.exe') @('check', '.')
    Invoke-Checked (Join-Path $ProjectRoot '.venv\Scripts\ruff.exe') @('format', '--check', 'src', 'tests', 'hardware_smoke_test.py', 'desktop_main.py')
    Invoke-Checked (Join-Path $ProjectRoot '.venv\Scripts\mypy.exe') @()
    Invoke-Checked $AppPython @('-m', 'PyInstaller', '--clean', '--noconfirm', 'RS02Configurator.spec')
    $Bundle = Join-Path $ProjectRoot 'dist\RS02Configurator'
    $Exe = Join-Path $Bundle 'RS02Configurator.exe'
    if (!(Test-Path $Exe)) { throw 'Expected executable was not created.' }
    if (!(Get-ChildItem $Bundle -Filter 'libusb-1.0.dll' -Recurse)) {
        throw 'Bundled libusb-1.0.dll missing; do not distribute this build.'
    }
    $Archive = Join-Path $ProjectRoot 'dist\RS02Configurator-Windows-x64-ja.zip'
    if (Test-Path $Archive) { Remove-Item $Archive }
    Compress-Archive -Path $Bundle -DestinationPath $Archive
    Write-Host "Build complete: $Exe"
    Write-Host "Distribute the entire folder or ZIP: $Archive"
    Write-Host 'Starts disconnected in simulated mode. Physical CAN is NOT verified by this build.'
    if ($Launch) {
        if ($env:QT_QPA_PLATFORM -eq 'offscreen') {
            throw 'Remove QT_QPA_PLATFORM=offscreen before launching the desktop GUI.'
        }
        Start-Process -FilePath $Exe -WorkingDirectory $ProjectRoot
    }
} finally {
    Pop-Location
}
