"""Cross-source release corroboration used by the public scorecard."""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.types import Event


@dataclass(frozen=True, slots=True)
class ReleaseCorroboration:
    version: str
    github: Event
    npm: Event
    difference_seconds: float


def corroborate_release(
    events: list[Event], *, repo: str, package: str, version: str
) -> ReleaseCorroboration | None:
    normalized_version = version.removeprefix("v")
    github = next(
        (
            event
            for event in events
            if event.kind == "release"
            and event.entity == repo
            and event.meta.get("source") == "gharchive"
            and str(event.meta.get("version", "")).removeprefix("v")
            == normalized_version
        ),
        None,
    )
    npm = next(
        (
            event
            for event in events
            if event.kind == "release"
            and event.entity == package
            and event.meta.get("source") == "npm_registry"
            and str(event.meta.get("version", "")).removeprefix("v")
            == normalized_version
        ),
        None,
    )
    if github is None or npm is None:
        return None
    return ReleaseCorroboration(
        version=normalized_version,
        github=github,
        npm=npm,
        difference_seconds=abs((npm.ts - github.ts).total_seconds()),
    )
