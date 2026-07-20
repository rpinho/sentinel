# Sentinel

An anomaly detection + triage agent for public event streams that **grades itself in public**.

The current core provides a source-neutral event contract, two deliberately different
public-data adapters, deterministic trailing-baseline detection, DuckDB persistence,
GPT-5.6 causal triage, exact-response replay fixtures, two real ablations, and an
append-only feedback loop. The public scorecard UI is the next pass.

## Install and test

```bash
uv sync --extra dev
uv run pytest -q
uv run sentinel --help
```

## Ingest any event stream

Every adapter produces this contract:

```json
{"ts":"2025-04-07T18:04:49Z","entity":"owner/repo","kind":"issue","text":"optional","value":null,"meta":{}}
```

`text` and `value` are independently optional. Pipe generic events directly:

```bash
some-event-producer | uv run sentinel ingest jsonl --db sentinel.duckdb
```

Or use either built-in adapter:

```bash
uv run sentinel ingest gharchive \
  --repo webpack/webpack \
  --start 2025-04-07T12:00:00Z \
  --end 2025-04-07T23:00:00Z

uv run sentinel ingest npm \
  --package webpack \
  --start 2025-04-07 \
  --end 2025-04-09
```

GH Archive files are cached under `.cache/gharchive`. Ingestion coverage is stored
per requested source window; missing hours are reported rather than assumed to be
zero activity.

## Detect

```bash
uv run sentinel detect \
  --entity webpack/webpack \
  --start 2025-04-07T12:00:00Z \
  --end 2025-04-07T23:00:00Z
```

Thresholds, baseline length, source event kinds, and the volume floor live in the
packaged TOML config. Each firing carries the config's SHA-256 version. Pass
`--config path/to/config.toml` to use a different committed configuration.

Detection runs GPT-5.6 triage for each firing by default. Use `--no-agent` for the
detector-only ablation.

## Triage, replay, and ablations

Triage reads only persisted events for the firing's entity with `event.ts < firing.ts`.
The store query uses an exclusive upper bound, and the triage boundary asserts the same
invariant again before hashing or calling the API. Events at the firing timestamp and
later events are rejected.

Run one persisted firing directly:

```bash
uv run sentinel triage FIRING_ID --db sentinel.duckdb
```

The live API key is loaded from the repository `.env` as `OPENAI_API_KEY`. Each request
uses GPT-5.6 with
[Responses structured output](https://developers.openai.com/api/docs/guides/structured-outputs)
and is hashed from the exact effective API payload. GPT-5.6's Responses endpoint rejects
non-default `temperature`, so Sentinel omits it. New live cache seeds include a fixed
`seed: 0` as best-effort sampling stability, and that seed is part of the request hash;
deterministic replay still comes from the exact cached response, not the seed. Historical
no-seed fixtures remain exact-replay compatible. Live responses are written to both
`.cache/triage/` and `tests/fixtures/triage/`. On a cache miss without a key, Sentinel
fails instead of inventing a response.

Triage uses a 24-hour lookback. Requests over 500,000 characters are compacted with the
versioned `signal-first-v1` policy: issue/release signals and the newest context rank
ahead of human comments and bot noise, individual text is an exact leading substring,
and the request records source/supplied counts and character totals. Rolling API rate
limits honor bounded `Retry-After` retries.

The modes are persisted side by side in `triage_results`:

- Default/full: GPT triage with active feedback from `relevant_feedback()`.
- `--no-feedback`: GPT triage without feedback; the changed request has a distinct hash.
- `--no-agent`: detector only; the scorecard firing remains, with null cause and culprit.

Full-mode feedback is rendered with IDs and provenance, delimited as untrusted historical
data, and can affect only agent context. It cannot alter detector metrics, thresholds,
baselines, tiers, or routing.

## Feedback

Label an emitted firing:

```bash
uv run sentinel feedback scorecard FIRING_ID \
  --outcome false_positive \
  --reason-code expected_business_event \
  --note "Scheduled migration"
```

Report a false negative, which has no firing to annotate:

```bash
uv run sentinel feedback miss \
  --entity webpack/webpack \
  --metric issue_activity_per_hour \
  --ts 2025-04-07T18:04:49Z \
  --cause "v5.99.0 runtime regression"
```

Feedback is immutable and append-only; corrections add a superseding row. The
bounded active context for a future triage call is inspectable with:

```bash
uv run sentinel feedback context \
  --entity webpack/webpack \
  --metric issue_activity_per_hour
```

Free-text notes are delimited and explicitly treated as untrusted historical data.
Feedback can change later triage context, but it cannot mutate deterministic metrics,
thresholds, tiers, or routing.

## Frozen holdout

Run the committed one-week, three-repository holdout and verify all 40 GPT fixtures
offline:

```bash
scripts/run_holdout.sh
```

The frozen detector produced 20 firings over 504/504 covered repo-hours. The disclosed
automatic-label rule resolved only two: the no-agent baseline had 1 TP and 1 FP (50%
precision, 1 FP/week), while full and no-feedback triage each rejected both (1 miss,
1 true negative, undefined precision). Eighteen firings remain `UNRESOLVED`, and three
acknowledged detector misses are published. The frozen feedback snapshot had no rows for
the holdout repositories, so no feedback benefit is claimed. See `HOLDOUT.md` for every
firing and the documented correction of two initial auto-label leakage bugs.

## Reproduce the webpack acceptance run

The full replay downloads eleven large GH Archive hours on its first run (roughly
1.7 GB compressed), ingests npm independently, reruns detection, and asserts the
public issue, maintainer acknowledgment, releases, patch, and download counts:

```bash
scripts/run_webpack_acceptance.sh
```

Current verified result:

- GH Archive coverage: 11/11 requested hours.
- Issue `#19394` observed at `2025-04-07T18:04:49Z`.
- Sentinel fired at `18:27:36Z`, on the sixth qualifying issue-activity event.
- Substantive maintainer acknowledgment appeared at `20:14:04Z`, 106.5 minutes later.
- `v5.99.0`: GH Archive `13:12:05Z`; npm `13:13:08.298Z` (63 seconds apart).
- `v5.99.1`: GH Archive `22:35:51Z`; npm `22:36:24.645Z` (34 seconds apart).
- npm reported 5,356,274 and 5,878,921 downloads/day on April 7–8.
- GPT-5.6 full and no-feedback triage both named `v5.99.0` at 0.98 confidence.
- The triage input ended at `18:27:04Z`, 32 seconds before the `18:27:36Z` firing.
- Both fixtures contain 12 pre-firing events, four exact issue-text quotes, and no
  `20:14:04Z` maintainer acknowledgment or PR `#19398` answer.
- Exact prompt hashes: full `d70b11c9...47f6`; no-feedback `4a1a3239...37f6`.
- Fixture replay succeeds without `OPENAI_API_KEY` and reports `cache_hit=true` and
  `replayed=true`.

See `HANDOFF.md` for the product brief, `TRIAGE.md` for the implemented agent contract,
and `FEEDBACK_LOOP.md` for the learning-loop rationale.
