from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sentinel.web.app import (
    PACKAGE_DIR,
    SCORECARD_BUNDLE_PATH,
    V1_SCORECARD_PATH,
    V2_SCORECARD_PATH,
    V3_SCORECARD_PATH,
    V4_SCORECARD_PATH,
    V5_SCORECARD_PATH,
    V6_SCORECARD_PATH,
    app,
    build_replay_view,
    build_scorecard_view,
    build_scorecard_view_from_sources,
    ingest_live_hour,
)
from sentinel.types import Event

client = TestClient(app)


def test_replay_recomputes_offline_from_source_and_fixture(monkeypatch: Any) -> None:
    def unexpected_live_call(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline web replay attempted a live provider call")

    monkeypatch.setattr("sentinel.triage.OpenAIBackend.create", unexpected_live_call)
    response = client.get("/replay/webpack")
    view = build_replay_view()

    assert response.status_code == 200
    assert "Sentinel named the release" in response.text
    assert "79<small>MIN</small>" in response.text
    assert "v5.99.0" in response.text
    assert "20:14:04 UTC" in response.text
    assert "network" in response.text.lower()
    assert "disabled for replay" in response.text
    assert view["source"]["event_count"] == 245
    assert view["triage"]["culprit_ref"] == "v5.99.0"
    assert view["triage"]["replayed"] is True
    assert view["triage"]["prompt_hash"] == (
        "42978ab2a88d53b077361e8749d54c3c5f63f3c54f9672be390c4308a10f3b4c"
    )
    fixture = json.loads(
        (
            PACKAGE_DIR
            / "data/triage/42978ab2a88d53b077361e8749d54c3c5f63f3c54f9672be390c4308a10f3b4c.json"
        ).read_text(encoding="utf-8")
    )
    assert fixture["requested_model"] == "gpt-5.6"
    assert fixture["returned_model"] == "gpt-5.6-sol"
    assert "temperature" not in fixture["request"]
    assert "seed" not in fixture["request"]
    assert view["firing"]["detector_version"] == "sha256:3b11a572823b"
    legacy = client.get("/replay/webpack-v5.99.0", follow_redirects=False)
    assert legacy.status_code == 308
    assert legacy.headers["location"] == "/replay/webpack"


def test_scorecard_publishes_v1_to_v6_and_complete_v6_miss_ledger() -> None:
    response = client.get("/scorecard")
    view = build_scorecard_view()

    assert response.status_code == 200
    assert "More evidence did not" in response.text
    assert "All 125 labeled misses are here" in response.text
    assert "This is a negative architecture result" in response.text
    assert [row["version"] for row in view["progression"]] == [
        "v1",
        "v2",
        "v3",
        "v4",
        "v5",
        "v6",
    ]
    assert len(view["v1_firings"]) == 20
    assert len(view["v1_misses"]) == 3
    assert len(view["v2_misses"]) == 4
    assert len(view["v6_misses"]) == 125
    assert view["v6_stage1_miss_count"] == 34
    assert view["v6_stage2_miss_count"] == 91
    assert response.text.count("MISS · STAGE") == 125
    for firing in view["v1_firings"]:
        assert firing["url"] in response.text
    for miss in view["v6_misses"]:
        assert miss["url"] in response.text
    for key in ("protocol_sha256", "request_bundle_sha256", "verdict_bundle_sha256"):
        assert view["v6_identities"][key] in response.text


def test_packaged_scorecards_are_exact_holdout_copies() -> None:
    assert V1_SCORECARD_PATH.read_bytes() == Path("holdout/scorecard.json").read_bytes()
    assert (
        V2_SCORECARD_PATH.read_bytes() == Path("holdout/v2/scorecard.json").read_bytes()
    )
    assert V3_SCORECARD_PATH == Path.cwd() / "holdout/v3/scorecard.json"
    assert V4_SCORECARD_PATH == Path.cwd() / "holdout/v4/scorecard.json"
    assert V5_SCORECARD_PATH == Path.cwd() / "holdout/v5/scorecard.json"
    assert V6_SCORECARD_PATH == Path.cwd() / "holdout/v6/scorecard.json"
    fixture_name = (
        "42978ab2a88d53b077361e8749d54c3c5f63f3c54f9672be390c4308a10f3b4c.json"
    )
    assert (PACKAGE_DIR / "data" / "triage" / fixture_name).read_bytes() == (
        Path("tests/fixtures/triage") / fixture_name
    ).read_bytes()
    assert SCORECARD_BUNDLE_PATH.exists()
    assert build_scorecard_view() == build_scorecard_view_from_sources()


def test_live_is_idle_until_post_and_surfaces_injected_run(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_live(repo: str) -> dict[str, Any]:
        calls.append(repo)
        return {
            "repo": repo,
            "hour": datetime(2026, 7, 20, 11, tzinfo=UTC),
            "archive_url": "https://data.gharchive.org/2026-07-20-11.json.gz",
            "event_count": 2,
            "inserted": 2,
            "firings": 0,
            "diagnostics": {"immature": 4},
            "covered_hours": 1,
            "history_ready": False,
            "history_hours_required": 169,
            "stage1": {
                "status": "immature",
                "model": "sha256:57021d95ec13",
                "threshold": 0.62,
                "candidates": [],
            },
            "stage2": {
                "status": "blocked_by_history",
                "detail": "Stage 2 waits for stage one.",
                "verdicts": [],
            },
            "events": [
                {
                    "ts": datetime(2026, 7, 20, 11, 4, tzinfo=UTC),
                    "url": "https://github.com/webpack/webpack/issues/20000",
                    "kind": "issue",
                    "issue_number": 20000,
                }
            ],
        }

    monkeypatch.setattr("sentinel.web.app.ingest_live_hour", fake_live)
    idle = client.get("/live")
    run = client.post("/live?repo=webpack%2Fwebpack")

    assert idle.status_code == 200
    assert "Waiting for an explicit run" in idle.text
    assert calls == ["webpack/webpack"]
    assert run.status_code == 200
    assert "Stage one is waiting for history" in run.text
    assert "Baseline state: immature" in run.text
    assert "2026-07-20 11:04:00 UTC" in run.text


def test_live_hour_uses_recent_archive_without_model_call(tmp_path: Path) -> None:
    class FakeAdapter:
        def archive_url(self, hour: datetime) -> str:
            return f"https://data.example/{hour.hour}.json.gz"

        def cache_path(self, hour: datetime) -> Path:
            return tmp_path / f"{hour.hour}.json.gz"

        def iter_events(self, hour: datetime, *, entity: str):
            yield Event(
                ts=hour + timedelta(minutes=4),
                entity=entity,
                kind="issue",
                text="A current public issue",
                meta={
                    "source": "gharchive",
                    "source_event_id": "live-1",
                    "url": "https://github.com/webpack/webpack/issues/20000",
                    "issue_number": 20000,
                    "action": "opened",
                    "actor": "reporter",
                },
            )

    result = ingest_live_hour(
        "webpack/webpack",
        now=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
        adapter=FakeAdapter(),  # type: ignore[arg-type]
        database_path=tmp_path / "live.duckdb",
    )

    assert result["hour"] == datetime(2026, 7, 20, 11, tzinfo=UTC)
    assert result["event_count"] == 1
    assert result["inserted"] == 1
    assert result["firings"] == 0
    assert result["history_ready"] is False
    assert result["covered_hours"] == 1
    assert result["stage1"]["status"] == "immature"
    assert result["stage2"]["status"] == "blocked_by_history"


def test_unknown_replay_and_invalid_live_repo_fail_closed() -> None:
    assert client.get("/replay/not-real").status_code == 404
    assert client.post("/live?repo=../../secrets").status_code == 422
