# Sentinel

An anomaly detection + triage agent for public event streams that **grades itself in public**.

The current core provides a source-neutral event contract, two deliberately different
public-data adapters, deterministic trailing-baseline detection, DuckDB persistence,
and an append-only feedback loop. Triage and the public scorecard UI are the next pass.

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

See `HANDOFF.md` for the product brief and `FEEDBACK_LOOP.md` for the learning-loop rationale.
