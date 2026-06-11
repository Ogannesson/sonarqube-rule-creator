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
- Exports existing Quality Profiles to PM-editable CSV/XLSX files.
- Syncs edited Profile Rules files back to SonarQube in patch or replace mode.
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
- `active`
- `sync_action`
- `source_profile`
- `profile_key`
- `rule_name`
- `inheritance`
- `severity`
- `params`
- `prioritizedRule`
- `project_key`
- `set_default`
- `note`

## Profile Export And Sync

After connecting to SonarQube, use `Profile export / sync` to export an existing profile to CSV/XLSX. PMs can edit the exported `Profile Rules` table.

- Patch mode processes only rows in the table.
- Replace mode also plans deactivation for active server rules missing from the table.
- Set `active=false` to explicitly deactivate a rule during sync.
- Leave `sync_action` blank for automatic diff, or use `activate`, `update`, `deactivate`, `noop`, or `skip`.

## SonarQube Permissions

The token needs permission to edit Quality Profiles. For most teams, use a token from an account with `Administer Quality Profiles`.

## Build Windows EXE

```powershell
python -m pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

The generated single-file app is placed at `dist\SonarQubeProfileCreator.exe`.

## Release

GitHub Actions runs tests on `main` and pull requests. Version tags build the Windows EXE and publish a GitHub Release.

```powershell
git tag v1.2.0
git push origin main
git push origin v1.2.0
```
