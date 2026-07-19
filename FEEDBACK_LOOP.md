# Anomaly Detector Feedback Loop

## Implementation status

The core loop is shipped. Scorecard verdicts and missed anomalies are append-only DuckDB
records; active, non-superseded feedback is rendered with provenance as untrusted context
before full-mode GPT-5.6 triage. `--no-feedback` removes that context from the exact hashed
request, and `--no-agent` persists a detector-only scorecard result. Mode-specific triage
results remain side by side and later scorecard feedback captures the latest full-mode
hypothesis and confidence.

The GitHub-issues ingestion channel, automatic maintainer labeling, calibration aggregation,
and web scorecard controls remain future delivery surfaces. They are not required for the
implemented contextual-learning mechanism.

## Decision

Use the anomaly scorecard as the primary feedback surface and store every response as structured, append-only feedback. Each Sentinel run reads relevant feedback before it performs triage.

This creates two distinct learning loops:

1. **Contextual learning (fast):** prior feedback becomes context for the agent on its next run. This can change the agent's investigation, explanation, and recommendation.
2. **Detector calibration (controlled):** accumulated feedback informs reviewed changes to the dbt model or rules. It never silently rewrites production logic.

The split is intentional. Agent triage is contextual and non-deterministic. Metric definitions and routing remain deterministic, versioned, and auditable.

## Scorecard feedback

Every anomaly scorecard should offer:

- **True positive** — the anomaly is real and useful.
- **False positive** — the detector fired, but it should not have.
- **Needs review** — the reviewer cannot confidently classify it yet.
- An optional reason code and free-text explanation.

A false negative has no existing scorecard to annotate. Add a **Report missed anomaly** action that captures the metric, entity or segment, expected time window, and explanation, then creates a retrospective scorecard linked to the feedback.

Useful reason codes include:

- Expected business event
- Known data-quality issue
- Wrong comparison group
- Baseline or seasonality problem
- Threshold too sensitive
- Threshold not sensitive enough
- Incorrect severity or routing
- Missing investigation context
- Other

## Feedback record

Store one immutable row per submission. Corrections should create another row rather than overwrite history.

| Field | Purpose |
| --- | --- |
| `feedback_id` | Stable identifier |
| `anomaly_id` | Links feedback to a detected or retrospectively created anomaly |
| `metric_id` | Scopes feedback to the relevant detector |
| `entity_key` | Optional customer, segment, region, or other affected entity |
| `outcome` | `true_positive`, `false_positive`, `false_negative`, or `needs_review` |
| `reason_code` | Structured explanation for aggregation |
| `comment` | Human context for later triage |
| `submitted_by` / `submitted_at` | Provenance |
| `detector_version` | Metric and rule version that produced the result |
| `sentinel_run_id` | Original run, when one exists |
| `supersedes_feedback_id` | Supports corrections without deleting history |

## Behavior on each Sentinel run

Before triage, the agent retrieves feedback that is:

- for the same metric, plus the same entity or segment when applicable;
- recent enough to remain relevant;
- still active and not superseded; and
- limited to a small, ranked set so stale comments do not overwhelm current evidence.

The triage prompt receives both a structured summary and the most relevant comments. The resulting scorecard records the feedback IDs consulted and explains when feedback materially affected the conclusion.

Feedback may influence:

- which hypotheses the agent investigates;
- contextual interpretation of a known event or data issue;
- the written explanation;
- the recommended follow-up; and
- a suggestion that the detector needs calibration.

Feedback must not directly change:

- metric definitions;
- numerators or denominators;
- z-score calculations;
- baselines or thresholds;
- rules-based tiers; or
- escalation routing.

Those changes belong in the deterministic layer.

## Calibration workflow

Aggregate feedback by metric, detector version, outcome, and reason code. When a pattern crosses a review threshold—for example, repeated false positives caused by the same baseline issue—create a calibration item with representative scorecards and feedback attached.

A reviewed change can then update the dbt model or rules that define:

- metric definition;
- numerator and denominator;
- z-score logic;
- baseline and seasonality treatment;
- threshold or tier; and
- escalation routing.

After deployment, stamp new anomalies with the updated detector version so feedback quality can be compared before and after the change.

## Guardrails

- Treat feedback as evidence, not as an instruction that overrides current data.
- Include submitter and timestamp provenance in agent context.
- Do not train or tune production logic automatically from a single response.
- Prevent prompt injection by clearly delimiting free text and instructing the agent to treat it only as historical reviewer commentary.
- Allow feedback to expire or be retired when the business context changes.
- Keep an audit trail of feedback read, decisions made, and detector versions used.

## MVP acceptance criteria

1. A reviewer can label a scorecard as true positive, false positive, or needs review and add a comment.
2. A reviewer can report a false negative even when no anomaly was emitted.
3. Feedback is persisted as structured, version-linked records.
4. A new Sentinel run retrieves relevant prior feedback before triage.
5. Its scorecard identifies which feedback records were consulted.
6. Feedback cannot directly mutate metric calculations or deterministic routing.
7. Repeated feedback can be summarized into a calibration item for a reviewed dbt or rules change.

## Minimal end-to-end flow

```text
scorecard response / missed-anomaly report
                    |
                    v
          append-only feedback store
                    |
             +------+------+
             |             |
             v             v
   next Sentinel run   feedback aggregation
   (triage context)           |
             |                v
             |        reviewed calibration item
             |                |
             v                v
       new scorecard      dbt/rules change
                              |
                              v
                     versioned detector
```

This is the first learning mechanism to ship. A Linear ticket or knowledge-base entry can remain an additional source later, but the scorecard-backed store should be the canonical feedback record because it is structured, attributable, and directly linked to detector behavior.
