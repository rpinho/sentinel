import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import RateLimitError

from sentinel.store import Firing
from sentinel.triage import (
    MAX_EVENT_TEXT_CHARS,
    MAX_TRIAGE_INPUT_CHARS,
    TriageCacheMissError,
    TriageInputError,
    TriageOutput,
    build_triage_request,
    triage_firing,
)
from sentinel.types import Event, parse_timestamp

FIRING_TS = datetime(2025, 4, 7, 18, 27, 36, tzinfo=UTC)


def _firing() -> Firing:
    return Firing(
        firing_id="firing:test",
        ts=FIRING_TS,
        entity="webpack/webpack",
        metric_id="issue_activity_per_hour",
        kind="metric",
        window_start=FIRING_TS.replace(minute=0, second=0),
        window_end=FIRING_TS.replace(minute=0, second=0) + timedelta(hours=1),
        observed=6,
        baseline_mean=1,
        baseline_stddev=1.4,
        score=3.5,
        detector_version="sha256:test",
        meta={"baseline_counts": [0, 0, 1, 1, 0, 4]},
    )


def _issue(ts: datetime | None = None, *, entity: str = "webpack/webpack") -> Event:
    return Event(
        ts=ts or datetime(2025, 4, 7, 18, 4, 49, tzinfo=UTC),
        entity=entity,
        kind="issue",
        text="Regression on v5.99.0: process is not defined",
        meta={"action": "opened", "issue_number": 19394},
    )


class FakeResponses:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=json.dumps(self.output),
            model="gpt-5.6-sol",
            id="resp_test",
        )


class FakeClient:
    def __init__(self, output: dict[str, Any]) -> None:
        self.responses = FakeResponses(output)


class RateLimitedResponses(FakeResponses):
    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            request = httpx.Request("POST", "https://api.openai.com/v1/responses")
            response = httpx.Response(
                429,
                headers={"retry-after": "0"},
                request=request,
            )
            raise RateLimitError(
                "Please try again in 0s.", response=response, body=None
            )
        return SimpleNamespace(
            output_text=json.dumps(self.output),
            model="gpt-5.6-sol",
            id="resp_retry",
        )


def _live_output() -> dict[str, Any]:
    return {
        "real": True,
        "cause": "The v5.99.0 release introduced a runtime regression.",
        "culprit_ref": "v5.99.0",
        "confidence": 0.91,
        "evidence_quotes": ["Regression on v5.99.0"],
    }


def test_post_firing_event_is_rejected_before_client_call(tmp_path: Path) -> None:
    client = FakeClient(_live_output())

    with pytest.raises(TriageInputError, match="not strictly before"):
        triage_firing(
            _firing(),
            [_issue(FIRING_TS)],
            client=client,
            cache_dir=tmp_path / "cache",
            fixture_dir=tmp_path / "fixtures",
        )

    assert client.responses.calls == []


def test_wrong_entity_is_rejected_before_client_call(tmp_path: Path) -> None:
    client = FakeClient(_live_output())

    with pytest.raises(TriageInputError, match="does not match firing"):
        triage_firing(
            _firing(),
            [_issue(entity="other/repo")],
            client=client,
            cache_dir=tmp_path / "cache",
            fixture_dir=tmp_path / "fixtures",
        )

    assert client.responses.calls == []


def test_live_response_is_cached_as_fixture_and_replays_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    fixture_dir = tmp_path / "fixtures"
    client = FakeClient(_live_output())
    event = _issue()

    live = triage_firing(
        _firing(),
        [event],
        feedback_context="No prior feedback.",
        client=client,
        cache_dir=cache_dir,
        fixture_dir=fixture_dir,
    )
    live_record = json.loads(
        (fixture_dir / f"{live.prompt_hash}.json").read_text(encoding="utf-8")
    )
    assert live_record["request"]["seed"] == 0
    assert "temperature" not in live_record["request"]
    (cache_dir / f"{live.prompt_hash}.json").unlink()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    replay = triage_firing(
        _firing(),
        [event],
        feedback_context="No prior feedback.",
        cache_dir=cache_dir,
        fixture_dir=fixture_dir,
    )

    assert live.cache_source == "live"
    assert live.cache_hit is False
    assert replay.cache_source == "fixture"
    assert replay.cache_hit is True
    assert replay.replayed is True
    assert replay.output == live.output
    assert len(client.responses.calls) == 1
    assert "temperature" not in client.responses.calls[0]
    assert client.responses.calls[0]["extra_body"] == {"seed": 0}


def test_live_rate_limit_retries_with_server_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = RateLimitedResponses(_live_output())
    client = SimpleNamespace(responses=responses)
    sleeps: list[float] = []
    monkeypatch.setattr("sentinel.triage.time.sleep", sleeps.append)

    run = triage_firing(
        _firing(),
        [_issue()],
        client=client,
        cache_dir=tmp_path / "cache",
        fixture_dir=tmp_path / "fixtures",
    )

    assert run.output.real is True
    assert len(responses.calls) == 2
    assert sleeps == [1.0]
    assert all("temperature" not in call for call in responses.calls)
    assert all(call["extra_body"] == {"seed": 0} for call in responses.calls)


def test_no_feedback_changes_exact_prompt_hash() -> None:
    full = build_triage_request(
        _firing(), [_issue()], feedback_context="No prior feedback."
    )
    ablated = build_triage_request(_firing(), [_issue()], no_feedback=True)

    assert full.mode == "full"
    assert ablated.mode == "no_feedback"
    assert full.prompt_hash != ablated.prompt_hash
    assert "temperature" not in full.api_payload
    assert "seed" not in full.api_payload
    assert '"enabled":false' in ablated.api_payload["input"][1]["content"]


def test_oversized_context_is_compacted_deterministically() -> None:
    issue = _issue(FIRING_TS - timedelta(minutes=59))
    bot_events = [
        Event(
            ts=FIRING_TS - timedelta(minutes=58 - index),
            entity="webpack/webpack",
            kind="comment",
            text=f"CI report {index}\n" + ("x" * 30_000),
            meta={"author": "metrics[bot]", "comment_id": index},
        )
        for index in range(58)
    ]
    events = [issue, *bot_events]

    first = build_triage_request(_firing(), events)
    second = build_triage_request(_firing(), list(reversed(events)))
    content = first.api_payload["input"][1]["content"]
    payload = json.loads(content)

    assert first.prompt_hash == second.prompt_hash
    assert len(content) <= MAX_TRIAGE_INPUT_CHARS
    assert payload["input_window"]["compaction"] == {
        "applied": True,
        "max_event_text_chars": MAX_EVENT_TEXT_CHARS,
        "policy": "signal-first-v1",
        "source_event_count": 59,
        "source_text_chars": sum(len(event.text or "") for event in events),
        "supplied_event_count": len(first.events),
        "supplied_text_chars": sum(len(event.text or "") for event in first.events),
        "target_event_context_chars": 350_000,
    }
    assert issue in first.events
    assert bot_events[-1].ts == first.events[-1].ts
    assert len(first.events) < len(events)
    assert all(len(event.text or "") <= MAX_EVENT_TEXT_CHARS for event in first.events)


def test_text_free_events_degrade_without_error(tmp_path: Path) -> None:
    client = FakeClient(
        {
            "real": False,
            "cause": "No textual evidence is available; timing alone is inconclusive.",
            "culprit_ref": None,
            "confidence": 0.2,
            "evidence_quotes": [],
        }
    )
    event = Event(
        ts=FIRING_TS - timedelta(minutes=30),
        entity="webpack/webpack",
        kind="release",
        text=None,
        meta={"version": "5.99.0"},
    )

    run = triage_firing(
        _firing(),
        [event],
        client=client,
        cache_dir=tmp_path / "cache",
        fixture_dir=tmp_path / "fixtures",
    )

    assert run.output.confidence == 0.2
    assert run.output.evidence_quotes == ()


def test_markdown_visible_quote_is_grounded_to_exact_source(tmp_path: Path) -> None:
    source = (
        "We figured it doesn't follow our "
        "[issue reporting guidelines](https://example.test/guidelines). "
        "Please update the issue."
    )
    client = FakeClient(
        {
            "real": False,
            "cause": "This is an issue-template burst rather than breakage.",
            "culprit_ref": None,
            "confidence": 0.95,
            "evidence_quotes": [
                "We figured it doesn't follow our issue reporting guidelines."
            ],
        }
    )
    event = Event(
        ts=FIRING_TS - timedelta(minutes=5),
        entity="webpack/webpack",
        kind="comment",
        text=source,
        meta={"action": "created"},
    )

    run = triage_firing(
        _firing(),
        [event],
        client=client,
        cache_dir=tmp_path / "cache",
        fixture_dir=tmp_path / "fixtures",
    )
    record = json.loads(
        (tmp_path / "fixtures" / f"{run.prompt_hash}.json").read_text()
    )

    assert run.output.evidence_quotes == (
        "We figured it doesn't follow our "
        "[issue reporting guidelines](https://example.test/guidelines).",
    )
    assert record["raw_response"] == client.responses.output
    assert record["evidence_grounding"] == [
        {
            "model_quote": (
                "We figured it doesn't follow our issue reporting guidelines."
            ),
            "grounded_quote": (
                "We figured it doesn't follow our "
                "[issue reporting guidelines](https://example.test/guidelines)."
            ),
            "method": "unique_markdown_visible_match",
        }
    ]


def test_percent_decoded_link_quote_is_grounded_to_exact_source(
    tmp_path: Path,
) -> None:
    source = (
        "[Add a changeset](https://example.test/new?value="
        "Update%20Rspack%20development%20test%20manifest)"
    )
    client = FakeClient(
        {
            "real": False,
            "cause": "This is routine manifest automation.",
            "culprit_ref": None,
            "confidence": 0.94,
            "evidence_quotes": ["Update Rspack development test manifest"],
        }
    )
    event = Event(
        ts=FIRING_TS - timedelta(minutes=5),
        entity="webpack/webpack",
        kind="comment",
        text=source,
        meta={"action": "created"},
    )

    run = triage_firing(
        _firing(),
        [event],
        client=client,
        cache_dir=tmp_path / "cache",
        fixture_dir=tmp_path / "fixtures",
    )
    record = json.loads(
        (tmp_path / "fixtures" / f"{run.prompt_hash}.json").read_text()
    )

    assert run.output.evidence_quotes == (source,)
    assert record["evidence_grounding"] == [
        {
            "model_quote": "Update Rspack development test manifest",
            "grounded_quote": source,
            "method": "unique_percent_decoded_markdown_url_match",
        }
    ]


def test_offline_cache_miss_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(TriageCacheMissError, match="OPENAI_API_KEY is absent"):
        triage_firing(
            _firing(),
            [_issue()],
            cache_dir=tmp_path / "cache",
            fixture_dir=tmp_path / "fixtures",
        )


def test_committed_webpack_fixtures_are_pre_firing_and_name_culprit() -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "triage"
    expected_modes = {
        "4a1a32397966ece3416134eec926a485afa869c0d57d17c61e3c2f00386837f6": False,
        "d70b11c9db7e918b945460220942cbccfc86657a8df830710f1953ab5df247f6": True,
    }
    firing_ts = parse_timestamp("2025-04-07T18:27:36Z")
    forbidden_ack = (
        "2784517989",
        "2025-04-07T20:14:04Z",
        "Found buggy places",
        "PR #19398",
    )

    for prompt_hash, feedback_enabled in expected_modes.items():
        fixture_path = fixture_dir / f"{prompt_hash}.json"
        serialized = fixture_path.read_text(encoding="utf-8")
        record = json.loads(serialized)
        request = record["request"]
        canonical = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == prompt_hash
        assert record["request_hash"] == prompt_hash
        assert request["model"] == "gpt-5.6"
        assert "temperature" not in request
        assert "seed" not in request
        assert all(marker not in serialized for marker in forbidden_ack)

        payload = json.loads(request["input"][1]["content"])
        events = payload["pre_firing_events"]
        assert payload["firing"]["firing_id"] == (
            "firing:9f151a8243fe964d47d56400"
        )
        assert len(events) == 12
        assert payload["input_window"]["max_event_ts"] == "2025-04-07T18:27:04Z"
        assert payload["feedback"]["enabled"] is feedback_enabled
        assert all(event["entity"] == "webpack/webpack" for event in events)
        assert all(parse_timestamp(event["ts"]) < firing_ts for event in events)

        output = TriageOutput.from_mapping(record["response"])
        source_texts = [
            " ".join((event.get("text") or "").split()) for event in events
        ]
        assert output.real is True
        assert output.culprit_ref == "v5.99.0"
        assert output.confidence == 0.98
        assert output.evidence_quotes
        assert all(
            any(" ".join(quote.split()) in source for source in source_texts)
            for quote in output.evidence_quotes
        )
