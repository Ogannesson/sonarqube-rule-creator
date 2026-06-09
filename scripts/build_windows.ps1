param(
    [string]$Name = "SonarQubeProfileCreator"
)

$ErrorActionPreference = "Stop"

Get-Process -Name $Name -ErrorAction SilentlyContinue | Stop-Process -Force

python -m pip install -r requirements-dev.txt
$env:PYTHONPATH = "src"
python -m sonarqube_profile_creator.main --create-templates templates | Out-Host

python -m PyInstaller `
    --noconfirm `
    --windowed `
    --name $Name `
    --paths src `
    --collect-data flet `
    --collect-all flet_desktop `
    --collect-all flet_web `
    --add-data "templates;templates" `
    --add-data "docs;docs" `
    src\sonarqube_profile_creator\main.py

Write-Host "Built dist\$Name"
