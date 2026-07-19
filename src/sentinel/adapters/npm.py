"""Text-free npm registry and downloads adapter."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from sentinel.types import Event, parse_timestamp

NPM_REGISTRY_URL = "https://registry.npmjs.org"
NPM_DOWNLOADS_URL = "https://api.npmjs.org/downloads/range"


class NpmAdapter:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        registry_url: str = NPM_REGISTRY_URL,
        downloads_url: str = NPM_DOWNLOADS_URL,
    ) -> None:
        self.client = client
        self.registry_url = registry_url.rstrip("/")
        self.downloads_url = downloads_url.rstrip("/")

    @staticmethod
    def _package_path(package: str) -> str:
        if not package.strip():
            raise ValueError("npm package cannot be empty")
        return quote(package.strip(), safe="")

    def _get_json(self, url: str) -> dict[str, Any]:
        owns_client = self.client is None
        client = self.client or httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=15.0),
        )
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.json()
        finally:
            if owns_client:
                client.close()

    def iter_releases(
        self,
        package: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Iterator[Event]:
        """Yield version publish events from the registry's ``time`` mapping."""

        package = package.strip()
        document = self._get_json(f"{self.registry_url}/{self._package_path(package)}")
        times = document.get("time")
        if not isinstance(times, dict):
            raise ValueError(
                f"npm registry response for {package!r} has no time mapping"
            )
        start_utc = parse_timestamp(start) if start else None
        end_utc = parse_timestamp(end) if end else None

        releases: list[tuple[datetime, str]] = []
        for version, timestamp in times.items():
            if version in {"created", "modified"}:
                continue
            published_at = parse_timestamp(timestamp)
            if start_utc and published_at < start_utc:
                continue
            if end_utc and published_at >= end_utc:
                continue
            releases.append((published_at, version))

        for published_at, version in sorted(releases):
            yield Event(
                ts=published_at,
                entity=package,
                kind="release",
                text=None,
                meta={
                    "source": "npm_registry",
                    "version": version,
                    "package": package,
                    "url": f"https://www.npmjs.com/package/{package}/v/{version}",
                },
            )

    def iter_downloads(self, package: str, start: date, end: date) -> Iterator[Event]:
        """Yield one text-free metric event per UTC day, inclusive."""

        if end < start:
            raise ValueError("npm download end date must be on or after start date")
        package = package.strip()
        path = self._package_path(package)
        document = self._get_json(
            f"{self.downloads_url}/{start.isoformat()}:{end.isoformat()}/{path}"
        )
        points = document.get("downloads")
        if not isinstance(points, list):
            raise ValueError(
                f"npm downloads response for {package!r} has no downloads list"
            )

        for point in points:
            day = date.fromisoformat(str(point["day"]))
            yield Event(
                ts=datetime(day.year, day.month, day.day, tzinfo=UTC),
                entity=package,
                kind="metric",
                text=None,
                value=float(point["downloads"]),
                meta={
                    "source": "npm_downloads",
                    "metric": "downloads_per_day",
                    "unit": "downloads/day",
                    "package": package,
                },
            )
