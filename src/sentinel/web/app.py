"""FastAPI surfaces that rerun Sentinel from source data on every replay."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sentinel.adapters.gharchive import GHArchiveAdapter
from sentinel.detect import DetectorConfig, evaluate_detection
from sentinel.feedback import render_feedback_context
from sentinel.store import IngestionWindow, SentinelStore
from sentinel.triage import triage_firing
from sentinel.types import Event, parse_timestamp

PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT_CANDIDATE = PACKAGE_DIR.parents[2]
PROJECT_ROOT = (
    SOURCE_ROOT_CANDIDATE
    if (SOURCE_ROOT_CANDIDATE / "pyproject.toml").exists()
    else Path.cwd()
)
STATE_DIR = Path(os.getenv("SENTINEL_STATE_DIR", ".cache"))
REPLAY_DATA_PATH = PACKAGE_DIR / "data" / "webpack-v5.99.0.json.gz"
PROJECT_TRIAGE_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "triage"
TRIAGE_FIXTURE_DIR = (
    PROJECT_TRIAGE_FIXTURE_DIR
    if PROJECT_TRIAGE_FIXTURE_DIR.exists()
    else PACKAGE_DIR / "data" / "triage"
)
TRIAGE_CACHE_DIR = STATE_DIR / "web-triage"
V1_SCORECARD_PATH = PACKAGE_DIR / "data" / "holdout-v1-scorecard.json"
V2_SCORECARD_PATH = PACKAGE_DIR / "data" / "holdout-v2-scorecard.json"
LIVE_CACHE_DIR = STATE_DIR / "live" / "gharchive"
LIVE_DB_PATH = STATE_DIR / "live" / "sentinel.duckdb"
CONFIG_PATH = PACKAGE_DIR.parent / "config" / "detector-v2.toml"

HERO_ID = "webpack-v5.99.0"
HERO_ENTITY = "webpack/webpack"
HISTORY_START = datetime(2025, 3, 21, tzinfo=UTC)
HERO_START = datetime(2025, 4, 7, 12, tzinfo=UTC)
HERO_END = datetime(2025, 4, 7, 23, tzinfo=UTC)
ACK_COMMENT_ID = 2784517989
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


def _timestamp(value: datetime | str) -> str:
    return parse_timestamp(value).strftime("%Y-%m-%d %H:%M:%S UTC")


def _confidence(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


templates.env.filters["timestamp"] = _timestamp
templates.env.filters["confidence"] = _confidence


def create_app() -> FastAPI:
    application = FastAPI(
        title="Sentinel",
        description="An anomaly detector that grades itself in public.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )
    application.mount(
        "/static",
        StaticFiles(directory=PACKAGE_DIR / "static"),
        name="static",
    )

    @application.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(f"/replay/{HERO_ID}", status_code=307)

    @application.get("/replay/{incident}", response_class=HTMLResponse)
    async def replay(request: Request, incident: str) -> HTMLResponse:
        if incident != HERO_ID:
            raise HTTPException(status_code=404, detail="unknown replay incident")
        replay_view = await run_in_threadpool(build_replay_view)
        return templates.TemplateResponse(
            request=request,
            name="replay.html",
            context={
                "active": "replay",
                "title": "Webpack v5.99.0 replay · Sentinel",
                "replay": replay_view,
            },
        )

    @application.get("/scorecard", response_class=HTMLResponse)
    async def scorecard(request: Request) -> HTMLResponse:
        view = await run_in_threadpool(build_scorecard_view)
        return templates.TemplateResponse(
            request=request,
            name="scorecard.html",
            context={
                "active": "scorecard",
                "title": "Frozen holdout scorecard · Sentinel",
                "scorecard": view,
            },
        )

    @application.get("/live", response_class=HTMLResponse)
    async def live(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="live.html",
            context={
                "active": "live",
                "title": "Live archive check · Sentinel",
                "live": None,
                "error": None,
            },
        )

    @application.post("/live", response_class=HTMLResponse)
    async def run_live(
        request: Request,
        repo: str = Query("webpack/webpack"),
    ) -> HTMLResponse:
        if not REPO_PATTERN.fullmatch(repo):
            raise HTTPException(status_code=422, detail="repo must be owner/name")
        result: dict[str, Any] | None = None
        error: str | None = None
        try:
            result = await run_in_threadpool(ingest_live_hour, repo)
        except Exception as exc:  # the UI should publish source failures honestly
            error = f"{type(exc).__name__}: {exc}"
        return templates.TemplateResponse(
            request=request,
            name="live.html",
            context={
                "active": "live",
                "title": "Live archive check · Sentinel",
                "live": result,
                "error": error,
            },
        )

    @application.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return application


@lru_cache(maxsize=1)
def load_replay_bundle() -> Mapping[str, Any]:
    with gzip.open(REPLAY_DATA_PATH, "rt", encoding="utf-8") as source:
        document = json.load(source)
    if document.get("schema_version") != 1:
        raise RuntimeError("unsupported replay source schema")
    rows = document.get("events")
    if not isinstance(rows, list):
        raise RuntimeError("replay source is missing normalized events")
    canonical = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected = document["source"]["normalized_events_sha256"]
    if hashlib.sha256(canonical).hexdigest() != expected:
        raise RuntimeError("replay source event hash mismatch")
    return document


def build_replay_view() -> dict[str, Any]:
    """Recompute detector and offline triage from committed source artifacts."""

    bundle = load_replay_bundle()
    events = [Event.from_dict(row) for row in bundle["events"]]
    config = DetectorConfig.load(CONFIG_PATH)
    detection = evaluate_detection(
        events,
        config,
        start=HERO_START,
        end=HERO_END,
    )
    if len(detection.firings) != 1:
        raise RuntimeError(
            f"webpack replay expected one firing; got {len(detection.firings)}"
        )
    firing = detection.firings[0]
    if firing.firing_id != "firing:07cb50298e4a5badf1114403":
        raise RuntimeError(f"unexpected replay firing {firing.firing_id}")

    pre_firing = [
        event
        for event in events
        if firing.ts - timedelta(hours=24) <= event.ts < firing.ts
    ]
    triage = triage_firing(
        firing,
        pre_firing,
        feedback_context=render_feedback_context([]),
        provider="openai",
        model="gpt-5.6",
        cache_dir=TRIAGE_CACHE_DIR,
        fixture_dir=TRIAGE_FIXTURE_DIR,
        allow_live=False,
    )
    release = _one_event(
        events,
        lambda event: (
            event.kind == "release"
            and event.meta.get("version") == "v5.99.0"
            and event.meta.get("source") == "gharchive"
        ),
        "v5.99.0 release",
    )
    first_report = _one_event(
        events,
        lambda event: event.kind == "issue" and event.meta.get("issue_number") == 19394,
        "issue #19394 report",
    )
    acknowledgement = _one_event(
        events,
        lambda event: event.meta.get("comment_id") == ACK_COMMENT_ID,
        "maintainer acknowledgement",
    )
    lead_minutes = round((acknowledgement.ts - firing.ts).total_seconds() / 60)
    metric = firing.meta["metric_evaluations"][firing.metric_id]["bot_excluded"]
    severity = firing.meta["severity"]

    return {
        "incident_id": HERO_ID,
        "entity": HERO_ENTITY,
        "release": _event_view(release),
        "first_report": _event_view(first_report),
        "firing": {
            "ts": firing.ts,
            "url": first_report.meta["url"],
            "metric": firing.metric_id,
            "observed": firing.observed,
            "baseline": firing.baseline_mean,
            "robust_z": firing.score,
            "numerator": metric["numerator"],
            "denominator": metric["denominator"],
            "detector_version": firing.detector_version,
            "severity": severity["severity"],
            "route": "digest queue",
            "interrupt_enabled": severity["interrupt_policy_enabled"],
        },
        "triage": {
            "real": triage.output.real,
            "cause": triage.output.cause,
            "culprit_ref": triage.output.culprit_ref,
            "confidence": triage.output.confidence,
            "evidence_quotes": triage.output.evidence_quotes,
            "provider": triage.provider,
            "model": triage.model,
            "prompt_hash": triage.prompt_hash,
            "cache_source": triage.cache_source,
            "replayed": triage.replayed,
            "input_max_ts": triage.input_max_ts,
        },
        "acknowledgement": _event_view(acknowledgement),
        "lead_minutes": lead_minutes,
        "source": bundle["source"],
        "config_sha256": _sha256(CONFIG_PATH),
        "diagnostics": detection.verdict_counts,
    }


@lru_cache(maxsize=1)
def build_scorecard_view() -> dict[str, Any]:
    v1 = json.loads(V1_SCORECARD_PATH.read_text(encoding="utf-8"))
    v2 = json.loads(V2_SCORECARD_PATH.read_text(encoding="utf-8"))

    firing_rows = []
    for firing in v1["firings"]:
        mode = firing["modes"]["full"]
        firing_rows.append(
            {
                "ts": firing["ts"],
                "url": firing["source_url"],
                "entity": firing["entity"],
                "hypothesis": mode["cause"],
                "confidence": mode["confidence"],
                "verdict": firing["verdict"]["verdict"],
                "mode": mode["mode"],
                "severity": firing["routing"]["severity"],
                "audience": firing["routing"]["audience"],
            }
        )

    return {
        "coverage": v2["coverage"],
        "v1_metrics": v1["metrics"],
        "v2_metrics": v2["metrics"],
        "v1_firings": firing_rows,
        "v1_misses": [_miss_view(row) for row in v1["detector_misses"]],
        "v2_misses": [_miss_view(row) for row in v2["detector_misses"]],
        "v1": {
            "config_hash": v1["holdout"]["detector_config_sha256"],
            "feedback_hash": v1["holdout"]["feedback_snapshot_sha256"],
            "detector_version": v1["holdout"]["detector_version"],
        },
        "v2": {
            "config_hash": v2["holdout"]["detector_config_sha256"],
            "feedback_hash": v2["holdout"]["feedback_snapshot_sha256"],
            "detector_version": v2["holdout"]["detector_version"],
            "diagnostics": v2["detector_diagnostics"],
        },
        "window": v2["detection_window"],
    }


def ingest_live_hour(
    repo: str,
    *,
    now: datetime | None = None,
    adapter: GHArchiveAdapter | None = None,
    database_path: Path = LIVE_DB_PATH,
) -> dict[str, Any]:
    """Fetch one recent completed archive hour and run the frozen detector."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    latest = current.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    source = adapter or GHArchiveAdapter(cache_dir=LIVE_CACHE_DIR)
    last_error: Exception | None = None
    chosen_hour: datetime | None = None
    events: list[Event] = []
    for lag in range(6):
        candidate = latest - timedelta(hours=lag)
        try:
            events = list(source.iter_events(candidate, entity=repo))
            chosen_hour = candidate
            break
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code != 404:
                raise
    if chosen_hour is None:
        raise RuntimeError(
            "no published GH Archive hour found in the last six hours"
        ) from last_error

    archive_path = source.cache_path(chosen_hour)
    with SentinelStore(database_path) as store:
        inserted = store.insert_events(events)
        store.record_ingestion_window(
            IngestionWindow(
                source="gharchive",
                entity=repo,
                window_start=chosen_hour,
                window_end=chosen_hour + timedelta(hours=1),
                status="complete",
                normalized_events=len(events),
                source_bytes=(
                    archive_path.stat().st_size if archive_path.exists() else None
                ),
                meta={"url": source.archive_url(chosen_hour)},
            )
        )
        history = store.list_events(
            entity=repo,
            start=chosen_hour - timedelta(days=14),
            end=chosen_hour + timedelta(hours=1),
        )
        coverage = store.list_ingestion_windows(
            entity=repo,
            source="gharchive",
            start=chosen_hour - timedelta(days=14),
            end=chosen_hour + timedelta(hours=1),
        )

    detector = evaluate_detection(
        history,
        DetectorConfig.load(CONFIG_PATH),
        start=chosen_hour,
        end=chosen_hour + timedelta(hours=1),
    )
    event_links = [_event_view(event) for event in events if event.meta.get("url")][:8]
    return {
        "repo": repo,
        "hour": chosen_hour,
        "archive_url": source.archive_url(chosen_hour),
        "event_count": len(events),
        "inserted": inserted,
        "firings": len(detector.firings),
        "diagnostics": detector.verdict_counts,
        "covered_hours": len(coverage),
        "history_ready": len(coverage) >= 24 * 14,
        "events": event_links,
    }


def _one_event(
    events: list[Event],
    predicate: Any,
    label: str,
) -> Event:
    matches = [event for event in events if predicate(event)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {label}; got {len(matches)}")
    return matches[0]


def _event_view(event: Event) -> dict[str, Any]:
    return {
        "ts": event.ts,
        "url": event.meta.get("url"),
        "text": event.text,
        "kind": event.kind,
        "issue_number": event.meta.get("issue_number"),
        "version": event.meta.get("version"),
    }


def _miss_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "entity": row["entity"],
        "issue_number": row["issue_number"],
        "reported_at": row["reported_at"],
        "report_url": row["report_url"],
        "report_excerpt": row["report_excerpt"],
        "ack_ts": row["ack_ts"],
        "ack_url": row["ack_url"],
        "ack_excerpt": row["ack_excerpt"],
        "reason": row["reason"],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


app = create_app()
