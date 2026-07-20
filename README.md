# Sentinel

An anomaly detection + triage agent for public event streams that **grades itself in public**.

The current core provides a source-neutral event contract, two deliberately different
public-data adapters, deterministic trailing-baseline detection, DuckDB persistence,
provider-agnostic causal triage, exact-response replay fixtures, two real ablations,
and an append-only feedback loop.

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

The packaged default is detector v2: phase-matched robust rates with explicit warmup,
exposure, automation-divergence, and observe-only verdicts. It requires the configured
14 days of continuous history; missing phase pools remain `immature` rather than being
zero-filled as fake evidence. Thresholds and metric contracts live in the packaged TOML
config, and each firing carries its SHA-256 version. Pass `--config
src/sentinel/config/default.toml` for the frozen v1 replay or another committed TOML for
a different detector.

Detection runs Claude triage for each firing by default. Provider-agnostic triage is a
shipped feature: run it with whichever key you have, or select the published GPT-5.6
path with `--provider openai`. Use `--no-agent` for the detector-only ablation.

## Triage, replay, and ablations

Triage reads only persisted events for the firing's entity with `event.ts < firing.ts`.
The store query uses an exclusive upper bound, and the triage boundary asserts the same
invariant again before hashing or calling the API. Events at the firing timestamp and
later events are rejected.

Run one persisted firing directly:

```bash
uv run sentinel triage FIRING_ID --db sentinel.duckdb
uv run sentinel triage FIRING_ID --provider openai --db sentinel.duckdb
```

Live keys are loaded from the repository `.env` as `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY`. Claude Sonnet 5 is the default; `--provider openai` selects GPT-5.6.
Both backends return the same strict triage contract. Provider, model, and exact effective
request are part of the cache identity, so Claude and OpenAI fixtures cannot collide and
published GPT fixtures remain valid.

The GPT-5.6 path uses
[Responses structured output](https://developers.openai.com/api/docs/guides/structured-outputs).
Its Responses endpoint rejects non-default `temperature`, so Sentinel omits it. Sentinel
attempts fixed `seed: 0` as a best-effort hint; if the endpoint explicitly rejects that
field as unsupported, it retries without seed and persists the canonical no-seed request
under its own exact hash. The webpack v2 publication run took that fallback. Deterministic
replay comes from the exact cached response, not a sampling control. Live responses from
either provider are written to both
`.cache/triage/` and `tests/fixtures/triage/`. On a cache miss without a key, Sentinel
fails instead of inventing a response.

Triage uses a 24-hour lookback. Requests over 500,000 characters are compacted with the
versioned `signal-first-v1` policy: issue/release signals and the newest context rank
ahead of human comments and bot noise, individual text is an exact leading substring,
and the request records source/supplied counts and character totals. Rolling API rate
limits honor bounded `Retry-After` retries.

The modes are persisted side by side in `triage_results`:

- Default/full: model triage with active feedback from `relevant_feedback()`.
- `--no-feedback`: model triage without feedback; the changed request has a distinct hash.
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

Run the committed one-week, three-repository detector v1 holdout and verify all 40 GPT
fixtures offline:

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

Detector v2 was frozen independently and then run on the exact same archive. It emitted
zero firings: all 20 v1 alerts disappeared, including 18 unresolved alerts, but the
published acknowledged-miss list increased from three to four. Precision is undefined,
FP/week is zero, and no feedback benefit can be measured. The result is not presented as
a recall improvement. See the v1/v2 comparison in `HOLDOUT.md` and the unedited v2 report
in `holdout/v2/HOLDOUT-v2.md`.

Holdout execution is cost-safe by default. `scripts/run_holdout_v2.sh` blanks the API key
and can only replay exact committed fixtures; it fails on a cache miss. The separately
named `scripts/publish_holdout_v2.sh` opts into live, potentially billable GPT fixture
seeding and is reserved for a final publication run. This frozen v2 result had no firings,
so its publication used zero GPT calls. Detector calibration and previews use no OpenAI
API calls.

## Reproduce the webpack acceptance run

The full replay downloads eleven large GH Archive hours on its first run (roughly
1.7 GB compressed), ingests npm independently, reruns detection, and asserts the
public issue, maintainer acknowledgment, releases, patch, and download counts:

```bash
scripts/run_webpack_acceptance.sh
```

Frozen v1 regression result:

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

The packaged v2 acceptance uses two weeks of continuous history and independently
corroborated GitHub/npm release data:

```bash
uv run python scripts/verify_detector_v2_acceptance.py \
  --db .cache/webpack-v2.duckdb \
  --config src/sentinel/config/detector-v2.toml
```

- Healthy contrast: zero firings.
- V2 fires at `18:55:02Z` on comments per active issue thread: `5.50` versus a
  phase-matched median of `1.00`, robust z `18.0`.
- The single-interval path is admitted only because GitHub and npm independently report
  v5.99.0 within 63 seconds.
- The firing is 79 minutes before the `20:14:04Z` substantive acknowledgment.
- GPT-5.6 full and no-feedback triage both name `v5.99.0` at 0.99 confidence from
  28 strictly pre-firing events; the newest input is `18:52:11Z`.
- Exact prompt hashes: full `8a8e84a6...af25`; no-feedback `96686e53...e4fd`.
- Both committed fixtures replay offline, omit temperature and unsupported seed, and
  exclude the later acknowledgment and PR `#19398`.

See `HANDOFF.md` for the product brief, `TRIAGE.md` for the implemented agent contract,
and `FEEDBACK_LOOP.md` for the learning-loop rationale.
