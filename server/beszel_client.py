from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests


class BeszelConfigError(RuntimeError):
    pass


class BeszelApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class BeszelCredentials:
    base_url: str
    email: str
    password: str


class BeszelClient:
    def __init__(self, credentials: BeszelCredentials, timeout: float = 8.0) -> None:
        self.credentials = credentials
        self.timeout = timeout
        self.session = requests.Session()
        self._token: str | None = None

    def authenticate(self) -> None:
        if not self.credentials.email or not self.credentials.password:
            raise BeszelConfigError("BESZEL_EMAIL and BESZEL_PASSWORD are required.")
        response = self.session.post(
            self._url("/api/collections/users/auth-with-password"),
            json={"identity": self.credentials.email, "password": self.credentials.password},
            timeout=self.timeout,
        )
        self._raise_for_status(response, "Beszel login failed")
        token = response.json().get("token")
        if not token:
            raise BeszelApiError("Beszel login response did not include a token.")
        self._token = token
        self.session.headers.update({"Authorization": token})

    def get_all(
        self,
        collection: str,
        *,
        filter_query: str | None = None,
        sort: str | None = None,
        page_size: int = 100,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        if not self._token:
            self.authenticate()
        items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            params: dict[str, Any] = {"page": page, "perPage": page_size}
            if filter_query:
                params["filter"] = filter_query
            if sort:
                params["sort"] = sort
            response = self.session.get(
                self._url(f"/api/collections/{collection}/records"),
                params=params,
                timeout=self.timeout,
            )
            if response.status_code == 401:
                # Token 可能已过期，重新鉴权并重试当前请求
                self.authenticate()
                response = self.session.get(
                    self._url(f"/api/collections/{collection}/records"),
                    params=params,
                    timeout=self.timeout,
                )
            self._raise_for_status(response, f"Beszel collection read failed: {collection}")
            payload = response.json()
            items.extend(payload.get("items") or [])
            if page >= int(payload.get("totalPages") or 1):
                break
        return items

    def snapshot(
        self,
        *,
        names: list[str] | None = None,
        ids: list[str] | None = None,
        minutes: int = 1440,
        sample_count: int | None = None,
        container_minutes: int = 1,
        record_type: str = "1m",
    ) -> dict[str, Any]:
        filters: list[str] = []
        if names:
            filters.append("(" + " || ".join(f'name = "{name}"' for name in names) + ")")
        if ids:
            filters.append("(" + " || ".join(f'id = "{system_id}"' for system_id in ids) + ")")
        systems = self.get_all("systems", filter_query=" && ".join(filters) if filters else None, sort="name")
        records: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for system in systems:
            system_id = system.get("id", "")
            stats = self._safe_stats("system_stats", system_id, sample_count or minutes, record_type, errors)
            if not stats and record_type != "1m":
                stats = self._safe_stats("system_stats", system_id, minutes, "1m", errors)
            container_stats = self._safe_stats("container_stats", system_id, container_minutes, "1m", errors)
            containers = self._safe_containers(system_id, errors)
            records.append(
                {
                    "system": self._normalise_system(system),
                    "latest": self._latest_summary(stats, container_stats),
                    "container_latest": self._latest_containers(container_stats, containers),
                    "cpu_history": _history(stats, "cpu"),
                    "memory_history": _history(stats, "mp"),
                    "disk_history": _history(stats, "dp"),
                }
            )
        return {
            "source": "beszel",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "record_type": record_type,
            "history_minutes": minutes,
            "systems": records,
            "errors": errors,
        }

    def _safe_stats(
        self,
        collection: str,
        system_id: str,
        minutes: int,
        record_type: str,
        errors: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        try:
            return self.get_all(
                collection,
                filter_query=f'system = "{system_id}" && type = "{record_type}"',
                sort="-created",
                page_size=max(1, min(minutes, 200)),
                max_pages=max(1, (minutes + 199) // 200),
            )
        except Exception as exc:
            errors.append({"system": system_id, "collection": collection, "error": str(exc)})
            return []

    def _safe_containers(self, system_id: str, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
        try:
            return self.get_all("containers", filter_query=f'system = "{system_id}"', sort="name")
        except Exception as exc:
            errors.append({"system": system_id, "collection": "containers", "error": str(exc)})
            return []

    def _url(self, path: str) -> str:
        return f"{self.credentials.base_url}{path}"

    @staticmethod
    def _raise_for_status(response: requests.Response, message: str) -> None:
        if not response.ok:
            raise BeszelApiError(f"{message}: HTTP {response.status_code} {response.text[:500]}")

    @staticmethod
    def _normalise_system(record: dict[str, Any]) -> dict[str, Any]:
        info = record.get("info") if isinstance(record.get("info"), dict) else {}
        return {
            "id": record.get("id"),
            "name": record.get("name") or info.get("hostname") or record.get("host"),
            "status": record.get("status"),
            "host": record.get("host"),
            "port": record.get("port"),
            "agent_version": info.get("v"),
            "uptime_seconds": info.get("u"),
            "cpu_threads": info.get("t"),
            "info": info,
        }

    @staticmethod
    def _latest_summary(system_stats: list[dict[str, Any]], container_stats: list[dict[str, Any]]) -> dict[str, Any]:
        latest = system_stats[0] if system_stats else {}
        stats = latest.get("stats") if isinstance(latest.get("stats"), dict) else {}
        container_record = container_stats[0] if container_stats else {}
        containers = container_record.get("stats") if isinstance(container_record.get("stats"), list) else []
        return {
            "created": latest.get("created"),
            "cpu_percent": _pick(stats, "cpu"),
            "memory_gb": _pick(stats, "m"),
            "memory_used_gb": _pick(stats, "mu"),
            "memory_percent": _pick(stats, "mp"),
            "disk_gb": _pick(stats, "d"),
            "disk_used_gb": _pick(stats, "du"),
            "disk_percent": _pick(stats, "dp"),
            "load_average": _pick(stats, "la"),
            "temperatures": _pick(stats, "t"),
            "container_count": len(containers),
        }

    @staticmethod
    def _latest_containers(
        container_stats: list[dict[str, Any]],
        containers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        latest_record = container_stats[0] if container_stats else {}
        latest_stats = latest_record.get("stats") if isinstance(latest_record.get("stats"), list) else []
        by_name = {container.get("name"): container for container in containers}
        out: list[dict[str, Any]] = []
        for item in latest_stats:
            if not isinstance(item, dict):
                continue
            name = item.get("n") or item.get("name")
            container = by_name.get(name, {})
            out.append(
                {
                    "name": name,
                    "cpu_percent": _pick(item, "c", "cpu"),
                    "memory_mb": _pick(item, "m", "memory"),
                    "status": container.get("status"),
                    "health": container.get("health"),
                    "image": container.get("image"),
                    "ports": container.get("ports"),
                }
            )
        return out


def compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot


def _history(records: list[dict[str, Any]], key: str) -> list[Any]:
    out: list[Any] = []
    for record in reversed(records):
        stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
        out.append(stats.get(key))
    return out


def _pick(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None
