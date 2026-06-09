# SonarQube Profile Creator

Windows app for PMs and non-developers to create SonarQube Quality Profiles from CSV or Excel rule lists.

## Features

- Connects to SonarQube with a token.
- Reads `.csv`, `.xlsx`, and `.xlsm` rule tables.
- Supports Chinese and English UI.
- Auto-detects common Chinese and English table headers.
- Creates Quality Profiles with four strategies:
  - Extend the language default profile
  - Extend a selected existing profile
  - Copy an existing profile
  - Create an independent profile
- Runs dry-run precheck before writing changes.
- Activates rules, binds projects, sets default profiles, and exports profile backups.
- Exports `report.json` and `report.xlsx`.

## Run From Source

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
python -m sonarqube_profile_creator.main
```

The app opens in a Flet Desktop window by default. Startup logs are written to `%APPDATA%\SonarQubeProfileCreator\startup.log`.

To use browser mode while developing:

```powershell
$env:PYTHONPATH = "src"
python -m sonarqube_profile_creator.main --web
```

## Create Example Templates

```powershell
$env:PYTHONPATH = "src"
python -m sonarqube_profile_creator.main --create-templates templates
```

## Required Table Columns

- `language`
- `target_profile`
- `rule_key`

## Optional Table Columns

- `parent_profile`
- `strategy`
- `severity`
- `params`
- `prioritizedRule`
- `project_key`
- `set_default`

## SonarQube Permissions

The token needs permission to edit Quality Profiles. For most teams, use a token from an account with `Administer Quality Profiles`.

## Build Windows EXE

```powershell
python -m pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

The generated single-file app is placed at `dist\SonarQubeProfileCreator.exe`.
