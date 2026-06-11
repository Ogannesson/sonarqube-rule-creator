from __future__ import annotations

import base64
import copy
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests

from .concurrency import bounded_parallel_map, client_read_workers


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
    def __init__(self, server_url: str, token: str, timeout: int = 20, max_read_workers: int = 8, max_write_workers: int = 4) -> None:
        self.server_url = server_url.rstrip("/") + "/"
        self.token = token.strip()
        self.timeout = timeout
        self.max_read_workers = max_read_workers
        self.max_write_workers = max_write_workers
        self._headers = {"User-Agent": "SonarQubeProfileCreator/0.1"}
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"
        self._local = threading.local()
        self._cache_lock = threading.Lock()
        self._profiles_cache: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
        self._profile_by_name_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        self._rule_cache: dict[str, dict[str, Any] | None] = {}
        self._active_rules_cache: dict[str, list[dict[str, Any]]] = {}
        self._rule_activation_cache: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self._headers)
            self._local.session = session
        return session

    def test_connection(self) -> ConnectionInfo:
        def load(name: str) -> tuple[str, Any]:
            if name == "status":
                return (name, self.get("api/system/status"))
            return (name, self.get_text("api/server/version"))

        loaded = dict(bounded_parallel_map(["status", "version"], load, client_read_workers(self, 2)))
        status_payload = loaded["status"]
        version = loaded["version"]
        return ConnectionInfo(
            server_url=self.server_url.rstrip("/"),
            version=version.strip(),
            status=str(status_payload.get("status", "")),
            auth_mode="Bearer token",
            capabilities={},
        )

    def load_capabilities(self) -> dict[str, bool]:
        webservices = self.get("api/webservices/list", params={"include_internals": "false"})
        return self._extract_capabilities(webservices)

    def search_quality_profiles(self, language: str = "", quality_profile: str = "", defaults: bool = False) -> list[dict[str, Any]]:
        cache_key = (language, quality_profile, defaults)
        with self._cache_lock:
            cached = self._profiles_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)
        params: dict[str, Any] = {}
        if language:
            params["language"] = language
        if quality_profile:
            params["qualityProfile"] = quality_profile
        if defaults:
            params["defaults"] = "true"
        payload = self.get("api/qualityprofiles/search", params=params)
        profiles = list(payload.get("profiles", []))
        with self._cache_lock:
            self._profiles_cache[cache_key] = copy.deepcopy(profiles)
        return profiles

    def get_default_profiles(self) -> dict[str, str]:
        cached_defaults = self._default_profiles_from_cached_all_profiles()
        if cached_defaults:
            return cached_defaults
        profiles = self.search_quality_profiles(defaults=True)
        result: dict[str, str] = {}
        for profile in profiles:
            language = str(profile.get("language", ""))
            name = str(profile.get("name", ""))
            if language and name:
                result[language] = name
        return result

    def _default_profiles_from_cached_all_profiles(self) -> dict[str, str]:
        with self._cache_lock:
            profiles = copy.deepcopy(self._profiles_cache.get(("", "", False), []))
        result: dict[str, str] = {}
        for profile in profiles:
            if not _profile_is_default(profile):
                continue
            language = str(profile.get("language", ""))
            name = str(profile.get("name", ""))
            if language and name:
                result[language] = name
        return result

    def get_profile_by_name(self, language: str, quality_profile: str) -> dict[str, Any] | None:
        cache_key = (language, quality_profile.casefold())
        with self._cache_lock:
            if cache_key in self._profile_by_name_cache:
                cached = self._profile_by_name_cache[cache_key]
                return copy.deepcopy(cached) if cached is not None else None
        for profile in self.search_quality_profiles(language=language, quality_profile=quality_profile):
            if str(profile.get("name", "")).casefold() == quality_profile.casefold():
                with self._cache_lock:
                    self._profile_by_name_cache[cache_key] = copy.deepcopy(profile)
                return profile
        with self._cache_lock:
            self._profile_by_name_cache[cache_key] = None
        return None

    def create_profile(self, language: str, name: str) -> dict[str, Any]:
        result = self.post("api/qualityprofiles/create", data={"language": language, "name": name})
        self.clear_cache()
        return result

    def copy_profile(self, from_key: str, to_name: str) -> dict[str, Any]:
        result = self.post("api/qualityprofiles/copy", data={"fromKey": from_key, "toName": to_name})
        self.clear_cache()
        return result

    def change_parent(self, language: str, quality_profile: str, parent_quality_profile: str = "") -> dict[str, Any]:
        data = {"language": language, "qualityProfile": quality_profile}
        if parent_quality_profile:
            data["parentQualityProfile"] = parent_quality_profile
        result = self.post("api/qualityprofiles/change_parent", data=data)
        self.clear_profile_cache(language, quality_profile)
        return result

    def show_rule(self, rule_key: str, actives: bool = False) -> dict[str, Any] | None:
        try:
            return self.get("api/rules/show", params={"key": rule_key, "actives": str(actives).lower()})
        except SonarQubeError as exc:
            if exc.status_code == 404:
                return None
            raise

    def search_rule(self, rule_key: str) -> dict[str, Any] | None:
        with self._cache_lock:
            if rule_key in self._rule_cache:
                cached = self._rule_cache[rule_key]
                return copy.deepcopy(cached) if cached is not None else None
        payload = self.get("api/rules/search", params={"rule_key": rule_key, "ps": 1})
        rules = payload.get("rules", [])
        if rules:
            rule = rules[0]
            with self._cache_lock:
                self._rule_cache[rule_key] = copy.deepcopy(rule)
            return rule
        with self._cache_lock:
            self._rule_cache[rule_key] = None
        return None

    def search_active_rules(self, profile_key: str) -> list[dict[str, Any]]:
        with self._cache_lock:
            cached = self._active_rules_cache.get(profile_key)
        if cached is not None:
            return copy.deepcopy(cached)
        page_size = 500
        first_payload = self._search_active_rules_page(profile_key, 1, page_size)
        first_batch = list(first_payload.get("rules", []))
        paging = first_payload.get("paging", {})
        total = int(paging.get("total", len(first_batch)) or 0)
        if total <= len(first_batch):
            with self._cache_lock:
                self._active_rules_cache[profile_key] = copy.deepcopy(first_batch)
            return first_batch
        effective_page_size = int(paging.get("pageSize", page_size) or page_size)
        if first_batch and total > len(first_batch) and len(first_batch) < effective_page_size:
            effective_page_size = len(first_batch)
        page_count = (total + effective_page_size - 1) // effective_page_size
        pages = list(range(2, page_count + 1))

        def load_page(page: int) -> list[dict[str, Any]]:
            payload = self._search_active_rules_page(profile_key, page, page_size)
            return list(payload.get("rules", []))

        rules = first_batch
        for batch in bounded_parallel_map(pages, load_page, client_read_workers(self, len(pages))):
            rules.extend(batch)
        with self._cache_lock:
            self._active_rules_cache[profile_key] = copy.deepcopy(rules)
        return rules

    def _search_active_rules_page(self, profile_key: str, page: int, page_size: int) -> dict[str, Any]:
        return self.get(
            "api/rules/search",
            params={
                "qprofile": profile_key,
                "activation": "true",
                "f": "actives,name,severity,params",
                "p": page,
                "ps": page_size,
            },
        )

    def show_rule_activation(self, rule_key: str, profile_key: str) -> dict[str, Any]:
        cache_key = (rule_key, profile_key)
        with self._cache_lock:
            cached = self._rule_activation_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)
        payload = self.show_rule(rule_key, actives=True) or {}
        actives = payload.get("actives", [])
        for active in actives:
            active_profile_key = str(active.get("qProfile") or active.get("qProfileKey") or active.get("profileKey") or "")
            if active_profile_key == profile_key:
                result = dict(active)
                with self._cache_lock:
                    self._rule_activation_cache[cache_key] = copy.deepcopy(result)
                return result
        with self._cache_lock:
            self._rule_activation_cache[cache_key] = {}
        return {}

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
        result = self.post("api/qualityprofiles/activate_rule", data=data)
        self.clear_profile_cache(profile_key=profile_key)
        return result

    def deactivate_rule(self, profile_key: str, rule_key: str) -> dict[str, Any]:
        result = self.post("api/qualityprofiles/deactivate_rule", data={"key": profile_key, "rule": rule_key})
        self.clear_profile_cache(profile_key=profile_key)
        return result

    def add_project(self, language: str, project_key: str, quality_profile: str) -> dict[str, Any]:
        return self.post(
            "api/qualityprofiles/add_project",
            data={"language": language, "project": project_key, "qualityProfile": quality_profile},
        )

    def set_default(self, language: str, quality_profile: str) -> dict[str, Any]:
        result = self.post(
            "api/qualityprofiles/set_default",
            data={"language": language, "qualityProfile": quality_profile},
        )
        self.clear_cache()
        return result

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
            "deactivate_rule": ("api/qualityprofiles", "deactivate_rule"),
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

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._profiles_cache.clear()
            self._profile_by_name_cache.clear()
            self._rule_cache.clear()
            self._active_rules_cache.clear()
            self._rule_activation_cache.clear()

    def clear_profile_cache(self, language: str = "", quality_profile: str = "", profile_key: str = "") -> None:
        with self._cache_lock:
            self._profiles_cache.clear()
            self._profile_by_name_cache.clear()
            if profile_key:
                self._active_rules_cache.pop(profile_key, None)
                for key in [key for key in self._rule_activation_cache if key[1] == profile_key]:
                    self._rule_activation_cache.pop(key, None)
            if language or quality_profile:
                self._active_rules_cache.clear()
                self._rule_activation_cache.clear()


def basic_auth_header(token: str) -> str:
    encoded = base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _profile_is_default(profile: dict[str, Any]) -> bool:
    for key in ("isDefault", "default", "is_default"):
        value = profile.get(key)
        if value is True:
            return True
        if isinstance(value, str) and value.strip().casefold() == "true":
            return True
    return False
