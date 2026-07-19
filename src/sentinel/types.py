"""Source-neutral contracts shared by Sentinel adapters and detectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping, TypeAlias, cast

EventKind: TypeAlias = Literal["issue", "comment", "release", "metric"]
EVENT_KINDS = frozenset({"issue", "comment", "release", "metric"})


def parse_timestamp(value: str | datetime) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC."""

    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("event timestamps must include a timezone")
    return parsed.astimezone(UTC)


def format_timestamp(value: datetime) -> str:
    """Render a UTC timestamp in the compact form used by public scorecards."""

    return parse_timestamp(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Event:
    """The normalized event contract every Sentinel adapter must satisfy."""

    ts: datetime
    entity: str
    kind: EventKind
    text: str | None = None
    value: float | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.entity.strip():
            raise ValueError("event entity cannot be empty")
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unsupported event kind: {self.kind!r}")
        object.__setattr__(self, "ts", parse_timestamp(self.ts))
        object.__setattr__(self, "entity", self.entity.strip())
        object.__setattr__(self, "meta", dict(self.meta))
        if self.value is not None:
            object.__setattr__(self, "value", float(self.value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": format_timestamp(self.ts),
            "entity": self.entity,
            "kind": self.kind,
            "text": self.text,
            "value": self.value,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Event:
        missing = {"ts", "entity", "kind"} - raw.keys()
        if missing:
            raise ValueError(f"event is missing required fields: {', '.join(sorted(missing))}")
        return cls(
            ts=parse_timestamp(cast(str | datetime, raw["ts"])),
            entity=str(raw["entity"]),
            kind=cast(EventKind, str(raw["kind"])),
            text=None if raw.get("text") is None else str(raw["text"]),
            value=None if raw.get("value") is None else float(raw["value"]),
            meta=cast(Mapping[str, Any], raw.get("meta") or {}),
        )
