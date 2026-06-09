from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import keyring


APP_NAME = "SonarQubeProfileCreator"
TOKEN_SERVICE = "sonarqube-profile-creator"


def user_config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".sonarqube-profile-creator"


@dataclass
class AppConfig:
    server_url: str = ""
    last_file: str = ""
    language: str = ""
    default_strategy: str = "extend_default"
    last_mapping: dict[str, str] = field(default_factory=dict)


class ConfigStore:
    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or user_config_dir()
        self.config_path = self.config_dir / "config.json"

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            return AppConfig()
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AppConfig()
        return AppConfig(
            server_url=str(data.get("server_url", "")),
            last_file=str(data.get("last_file", "")),
            language=str(data.get("language", "")),
            default_strategy=str(data.get("default_strategy", "extend_default")),
            last_mapping=dict(data.get("last_mapping", {})),
        )

    def save(self, config: AppConfig) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "server_url": config.server_url,
            "last_file": config.last_file,
            "language": config.language,
            "default_strategy": config.default_strategy,
            "last_mapping": config.last_mapping,
        }
        self.config_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_token(self, server_url: str) -> str:
        if not server_url:
            return ""
        try:
            return keyring.get_password(TOKEN_SERVICE, server_url) or ""
        except Exception:
            return ""

    def save_token(self, server_url: str, token: str) -> None:
        if not server_url or not token:
            return
        try:
            keyring.set_password(TOKEN_SERVICE, server_url, token)
        except Exception:
            return


def reports_dir() -> Path:
    path = user_config_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path

