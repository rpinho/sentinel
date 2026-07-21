import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate_stage2_v7.py"
SPEC = importlib.util.spec_from_file_location("evaluate_stage2_v7", SCRIPT)
assert SPEC and SPEC.loader
evaluate_stage2_v7 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate_stage2_v7)


def test_stories_deduplicate_siblings_and_match_exact_issue_only() -> None:
    candidates = {
        "thread": _candidate("thread", "issue_thread_activity", issue_number=12),
        "rate": _candidate("rate", "repository_event_type_rate"),
        "false": _candidate("false", "issue_thread_activity", issue_number=99),
    }
    findings = [
        _finding("thread", "issue:12", [12]),
        _finding("rate", "issue:12", [12]),
        _finding("false", "issue:99", [99]),
    ]
    stories = evaluate_stage2_v7._build_stories(findings, candidates)
    incidents = [
        {
            "incident_id": "incident:12",
            "entity": "example/repo",
            "issue_number": 12,
            "row_id": "row:positive",
            "confirmation_ts": "2025-05-05T14:00:00Z",
        }
    ]
    evaluate_stage2_v7._match_stories(stories, incidents)

    assert len(stories) == 2
    true_story = next(story for story in stories if story["story_key"] == "issue:12")
    assert true_story["candidate_ids"] == ["rate", "thread"]
    assert true_story["matched_incident_ids"] == ["incident:12"]
    false_story = next(story for story in stories if story["story_key"] == "issue:99")
    assert false_story["true_positive"] is False


def _candidate(candidate_id: str, series: str, issue_number: int | None = None) -> dict:
    features = {} if issue_number is None else {"issue_number": issue_number}
    return {
        "candidate_id": candidate_id,
        "row_id": f"row:{candidate_id}",
        "entity": "example/repo",
        "candidate_ts": "2025-05-05T13:00:00Z",
        "identity": {"series": series},
        "features": features,
    }


def _finding(candidate_id: str, story_key: str, related: list[int]) -> dict:
    return {
        "candidate_id": candidate_id,
        "response": {
            "severity": "medium",
            "story_key": story_key,
            "related_issue_numbers": related,
            "summary": "breakage",
            "evidence_quotes": ["crash"],
            "attribution_status": "no_claim",
        },
    }
