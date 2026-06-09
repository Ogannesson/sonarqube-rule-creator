param(
    [string]$Name = "SonarQubeProfileCreator"
)

$ErrorActionPreference = "Stop"

Get-Process -Name $Name -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name "flet" -ErrorAction SilentlyContinue | Where-Object {
    $_.MainWindowTitle -eq "SonarQube Profile Creator"
} | Stop-Process -Force

python -m pip install -r requirements-dev.txt
$env:PYTHONPATH = "src"
python -m sonarqube_profile_creator.main --create-templates templates | Out-Host
python scripts\generate_icon.py | Out-Host

$fletVersion = python -c "import flet_desktop.version; print(flet_desktop.version.version)"
$fletArchiveDir = Join-Path $PSScriptRoot "..\.build\flet_desktop"
$fletArchivePath = Join-Path $fletArchiveDir "flet-windows.zip"
New-Item -ItemType Directory -Force -Path $fletArchiveDir | Out-Null
if (-not (Test-Path $fletArchivePath)) {
    $fletUrl = "https://github.com/flet-dev/flet/releases/download/v$fletVersion/flet-windows.zip"
    Write-Host "Downloading Flet Desktop runtime $fletVersion"
    Invoke-WebRequest -Uri $fletUrl -OutFile $fletArchivePath
}

$distDir = Join-Path $PSScriptRoot "..\dist"
$distExe = Join-Path $distDir "$Name.exe"
$distFolder = Join-Path $distDir $Name
if (Test-Path $distExe) {
    Remove-Item -LiteralPath $distExe -Force
}
if (Test-Path $distFolder) {
    Remove-Item -LiteralPath $distFolder -Recurse -Force
}

python -m PyInstaller `
    --noconfirm `
    --windowed `
    --onefile `
    --name $Name `
    --icon "assets\app_icon.ico" `
    --paths src `
    --collect-data flet `
    --collect-all flet_desktop `
    --collect-all flet_web `
    --add-data "$fletArchivePath;flet_desktop/app" `
    --add-data "assets;assets" `
    --add-data "templates;templates" `
    --add-data "docs;docs" `
    src\sonarqube_profile_creator\main.py

Write-Host "Built dist\$Name.exe"
