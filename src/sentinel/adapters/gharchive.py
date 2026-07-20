"""GH Archive adapter for hourly gzip-compressed NDJSON event dumps."""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Collection, Iterator, Mapping

import httpx

from sentinel.types import Event, parse_timestamp

GHARCHIVE_BASE_URL = "https://data.gharchive.org"


class GHArchiveFormatError(RuntimeError):
    """Raised when an archive exists but does not match the documented shape."""


class GHArchiveAdapter:
    def __init__(
        self,
        cache_dir: Path | str = Path(".cache/gharchive"),
        *,
        client: httpx.Client | None = None,
        base_url: str = GHARCHIVE_BASE_URL,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.client = client
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def normalize_hour(hour: datetime) -> datetime:
        normalized = parse_timestamp(hour)
        return normalized.replace(minute=0, second=0, microsecond=0)

    def archive_name(self, hour: datetime) -> str:
        hour = self.normalize_hour(hour)
        # GH Archive explicitly does not zero-pad the hour component.
        return f"{hour:%Y-%m-%d}-{hour.hour}.json.gz"

    def archive_url(self, hour: datetime) -> str:
        return f"{self.base_url}/{self.archive_name(hour)}"

    def cache_path(self, hour: datetime) -> Path:
        return self.cache_dir / self.archive_name(hour)

    def download_hour(self, hour: datetime, *, force: bool = False) -> Path:
        """Download an hour atomically and return its local cache path."""

        destination = self.cache_path(hour)
        if destination.exists() and destination.stat().st_size > 0 and not force:
            return destination

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        owns_client = self.client is None
        client = self.client or httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(180.0, connect=20.0),
        )
        try:
            with client.stream("GET", self.archive_url(hour)) as response:
                response.raise_for_status()
                with partial.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            partial.replace(destination)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        finally:
            if owns_client:
                client.close()
        return destination

    def iter_events(
        self,
        hour: datetime,
        *,
        entity: str | None = None,
        entities: Collection[str] | None = None,
        force: bool = False,
    ) -> Iterator[Event]:
        """Yield normalized issue, comment, and release events for an hour."""

        if entity is not None and entities is not None:
            raise ValueError("pass entity or entities, not both")
        normalized_hour = self.normalize_hour(hour)
        path = self.download_hour(normalized_hour, force=force)
        entity_filter = (
            {entity.casefold()}
            if entity
            else {item.casefold() for item in entities or ()}
        )

        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="strict") as archive:
                for line_number, line in enumerate(archive, start=1):
                    try:
                        raw = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise GHArchiveFormatError(
                            f"invalid NDJSON in {path} at line {line_number}: {exc}"
                        ) from exc
                    repo_name = str((raw.get("repo") or {}).get("name") or "")
                    if entity_filter and repo_name.casefold() not in entity_filter:
                        continue
                    event = self._normalize_event(raw, normalized_hour)
                    if event is not None:
                        yield event
        except (gzip.BadGzipFile, EOFError) as exc:
            raise GHArchiveFormatError(
                f"invalid or truncated gzip archive: {path}"
            ) from exc

    @staticmethod
    def _normalize_event(
        raw: Mapping[str, Any], archive_hour: datetime
    ) -> Event | None:
        event_type = raw.get("type")
        if event_type not in {"IssuesEvent", "IssueCommentEvent", "ReleaseEvent"}:
            return None

        repo_name = str((raw.get("repo") or {}).get("name") or "").strip()
        if not repo_name:
            raise GHArchiveFormatError("supported event is missing repo.name")

        payload = raw.get("payload") or {}
        actor = raw.get("actor") or {}
        common_meta: dict[str, Any] = {
            "source": "gharchive",
            "source_event_id": str(raw.get("id") or ""),
            "source_event_type": event_type,
            "archive_hour": archive_hour.isoformat().replace("+00:00", "Z"),
            "archive_created_at": raw.get("created_at"),
            "actor": actor.get("login"),
            "actor_type": actor.get("type"),
            "action": payload.get("action"),
        }

        if event_type == "IssuesEvent":
            issue = payload.get("issue") or {}
            title = str(issue.get("title") or "").strip()
            body = str(issue.get("body") or "").strip()
            text = "\n\n".join(part for part in (title, body) if part) or None
            return Event(
                ts=parse_timestamp(raw["created_at"]),
                entity=repo_name,
                kind="issue",
                text=text,
                meta={
                    **common_meta,
                    "issue_number": issue.get("number"),
                    "issue_created_at": issue.get("created_at"),
                    "url": issue.get("html_url"),
                    "author": (issue.get("user") or {}).get("login"),
                    "author_type": (issue.get("user") or {}).get("type"),
                    "is_pull_request": bool(issue.get("pull_request")),
                    "labels": [
                        label.get("name")
                        for label in issue.get("labels") or []
                        if isinstance(label, Mapping) and label.get("name")
                    ],
                },
            )

        if event_type == "IssueCommentEvent":
            issue = payload.get("issue") or {}
            comment = payload.get("comment") or {}
            return Event(
                ts=parse_timestamp(raw["created_at"]),
                entity=repo_name,
                kind="comment",
                text=str(comment.get("body") or "").strip() or None,
                meta={
                    **common_meta,
                    "issue_number": issue.get("number"),
                    "comment_id": comment.get("id"),
                    "comment_created_at": comment.get("created_at"),
                    "url": comment.get("html_url"),
                    "author": (comment.get("user") or {}).get("login"),
                    "author_type": (comment.get("user") or {}).get("type"),
                    "author_association": comment.get("author_association"),
                    "issue_state": issue.get("state"),
                    "is_pull_request": bool(issue.get("pull_request")),
                },
            )

        release = payload.get("release") or {}
        name = str(release.get("name") or "").strip()
        body = str(release.get("body") or "").strip()
        text = "\n\n".join(part for part in (name, body) if part) or None
        return Event(
            # GH Archive's envelope timestamp is when this public stream observed
            # the release. Keep GitHub's intrinsic publish time separately; the
            # scorecard can show both instead of silently conflating them.
            ts=parse_timestamp(raw["created_at"]),
            entity=repo_name,
            kind="release",
            text=text,
            meta={
                **common_meta,
                "release_id": release.get("id"),
                "version": release.get("tag_name"),
                "published_at": release.get("published_at"),
                "url": release.get("html_url"),
                "prerelease": bool(release.get("prerelease")),
                "draft": bool(release.get("draft")),
            },
        )
