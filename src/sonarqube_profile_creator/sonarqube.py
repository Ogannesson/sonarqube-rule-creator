from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests


class SonarQubeError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class ConnectionInfo:
    server_url: str
    version: str
    status: str
    auth_mode: str
    capabilities: dict[str, bool]


class SonarQubeClient:
    def __init__(self, server_url: str, token: str, timeout: int = 20) -> None:
        self.server_url = server_url.rstrip("/") + "/"
        self.token = token.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "SonarQubeProfileCreator/0.1"})
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def test_connection(self) -> ConnectionInfo:
        status_payload = self.get("api/system/status")
        version = self.get_text("api/server/version")
        webservices = self.get("api/webservices/list", params={"include_internals": "false"})
        capabilities = self._extract_capabilities(webservices)
        return ConnectionInfo(
            server_url=self.server_url.rstrip("/"),
            version=version.strip(),
            status=str(status_payload.get("status", "")),
            auth_mode="Bearer token",
            capabilities=capabilities,
        )

    def search_quality_profiles(self, language: str = "", quality_profile: str = "", defaults: bool = False) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if language:
            params["language"] = language
        if quality_profile:
            params["qualityProfile"] = quality_profile
        if defaults:
            params["defaults"] = "true"
        payload = self.get("api/qualityprofiles/search", params=params)
        return list(payload.get("profiles", []))

    def get_default_profiles(self) -> dict[str, str]:
        profiles = self.search_quality_profiles(defaults=True)
        result: dict[str, str] = {}
        for profile in profiles:
            language = str(profile.get("language", ""))
            name = str(profile.get("name", ""))
            if language and name:
                result[language] = name
        return result

    def get_profile_by_name(self, language: str, quality_profile: str) -> dict[str, Any] | None:
        for profile in self.search_quality_profiles(language=language, quality_profile=quality_profile):
            if str(profile.get("name", "")).casefold() == quality_profile.casefold():
                return profile
        return None

    def create_profile(self, language: str, name: str) -> dict[str, Any]:
        return self.post("api/qualityprofiles/create", data={"language": language, "name": name})

    def copy_profile(self, from_key: str, to_name: str) -> dict[str, Any]:
        return self.post("api/qualityprofiles/copy", data={"fromKey": from_key, "toName": to_name})

    def change_parent(self, language: str, quality_profile: str, parent_quality_profile: str = "") -> dict[str, Any]:
        data = {"language": language, "qualityProfile": quality_profile}
        if parent_quality_profile:
            data["parentQualityProfile"] = parent_quality_profile
        return self.post("api/qualityprofiles/change_parent", data=data)

    def show_rule(self, rule_key: str, actives: bool = False) -> dict[str, Any] | None:
        try:
            return self.get("api/rules/show", params={"key": rule_key, "actives": str(actives).lower()})
        except SonarQubeError as exc:
            if exc.status_code == 404:
                return None
            raise

    def search_rule(self, rule_key: str) -> dict[str, Any] | None:
        payload = self.get("api/rules/search", params={"rule_key": rule_key, "ps": 1})
        rules = payload.get("rules", [])
        if rules:
            return rules[0]
        return None

    def activate_rule(
        self,
        profile_key: str,
        rule_key: str,
        severity: str = "",
        params: str = "",
        prioritized_rule: str = "",
    ) -> dict[str, Any]:
        data = {"key": profile_key, "rule": rule_key}
        if severity:
            data["severity"] = severity
        if params:
            data["params"] = params
        if prioritized_rule:
            data["prioritizedRule"] = prioritized_rule
        return self.post("api/qualityprofiles/activate_rule", data=data)

    def add_project(self, language: str, project_key: str, quality_profile: str) -> dict[str, Any]:
        return self.post(
            "api/qualityprofiles/add_project",
            data={"language": language, "project": project_key, "qualityProfile": quality_profile},
        )

    def set_default(self, language: str, quality_profile: str) -> dict[str, Any]:
        return self.post(
            "api/qualityprofiles/set_default",
            data={"language": language, "qualityProfile": quality_profile},
        )

    def backup_profile(self, language: str, quality_profile: str) -> bytes:
        return self.request(
            "GET",
            "api/qualityprofiles/backup",
            params={"language": language, "qualityProfile": quality_profile},
            raw=True,
        )

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.request("GET", endpoint, params=params)
        if isinstance(payload, dict):
            return payload
        return {}

    def get_text(self, endpoint: str, params: dict[str, Any] | None = None) -> str:
        return str(self.request("GET", endpoint, params=params, text=True))

    def post(self, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.request("POST", endpoint, data=data)
        if isinstance(payload, dict):
            return payload
        return {}

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        text: bool = False,
        raw: bool = False,
    ) -> Any:
        url = urljoin(self.server_url, endpoint)
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                data=data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SonarQubeError(str(exc)) from exc
        if response.status_code >= 400:
            raise SonarQubeError(
                self._error_message(response),
                status_code=response.status_code,
                payload=self._safe_json(response),
            )
        if raw:
            return response.content
        if text:
            return response.text
        if not response.content:
            return {}
        return self._safe_json(response)

    def _error_message(self, response: requests.Response) -> str:
        payload = self._safe_json(response)
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            messages = [str(item.get("msg", "")) for item in errors if isinstance(item, dict)]
            return "; ".join(message for message in messages if message) or response.text
        return response.text or f"HTTP {response.status_code}"

    def _safe_json(self, response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {}

    def _extract_capabilities(self, webservices: dict[str, Any]) -> dict[str, bool]:
        wanted = {
            "create_profile": ("api/qualityprofiles", "create"),
            "change_parent": ("api/qualityprofiles", "change_parent"),
            "copy_profile": ("api/qualityprofiles", "copy"),
            "activate_rule": ("api/qualityprofiles", "activate_rule"),
            "add_project": ("api/qualityprofiles", "add_project"),
            "set_default": ("api/qualityprofiles", "set_default"),
            "backup": ("api/qualityprofiles", "backup"),
            "show_rule": ("api/rules", "show"),
            "search_rule": ("api/rules", "search"),
        }
        services = webservices.get("webServices", [])
        indexed: set[tuple[str, str]] = set()
        for service in services:
            path = str(service.get("path", ""))
            for action in service.get("actions", []):
                indexed.add((path, str(action.get("key", ""))))
        return {name: target in indexed for name, target in wanted.items()}


def basic_auth_header(token: str) -> str:
    encoded = base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"

