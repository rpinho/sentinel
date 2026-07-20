# Sentinel triage contract

## What shipped

The deterministic detector answers whether activity crossed a threshold. GPT-5.6 triage
answers the load-bearing question the threshold cannot: whether the burst represents real
breakage and which preceding release, commit, or pull request most likely caused it.

The structured result is:

```json
{
  "real": true,
  "cause": "concise causal explanation",
  "culprit_ref": "v5.99.0",
  "confidence": 0.98,
  "evidence_quotes": ["exact source text"]
}
```

## Temporal isolation

The hero claim is valid only if the agent names the culprit before maintainers reveal it.
Sentinel enforces this at two boundaries:

1. `SentinelStore.list_events(entity=..., end=firing.ts)` uses SQL `ts < ?`.
2. `build_triage_request()` asserts every event has the firing entity and `event.ts < firing.ts`
   before it computes the request hash or calls GPT-5.6.

Evidence quotes are normalized only for comparison and must occur in supplied event text.
A real incident with text events must include at least one quote. Text-free streams degrade
to lower-confidence timing/metadata reasoning with an empty quote list.

The committed webpack fixtures are regression-tested for exact request hashes, the strict
timestamp boundary, same-entity scope, real source quotes, and absence of the later
maintainer acknowledgment.

## API and replay

Sentinel uses the
[OpenAI Responses API structured-output contract](https://developers.openai.com/api/docs/guides/structured-outputs)
with GPT-5.6 and strict `text.format` JSON schema.
The live endpoint rejects `temperature: 0`, so temperature is omitted entirely. Sentinel
attempts fixed `seed: 0` as a best-effort reproducibility hint. During the final webpack v2
fixture run, the Responses endpoint returned HTTP 400 `Unknown parameter: 'seed'`.
Sentinel now retries only that explicit unsupported-parameter failure without seed and
persists the successful canonical request under its no-seed hash. The source of truth for
reproducibility is the SHA-256 of the exact effective request plus its committed response
fixture, not temperature or seed.

The event lookup is bounded to 24 hours. If the rendered input exceeds 500,000 characters,
`signal-first-v1` deterministically retains issue/release signals and recent context before
human comments and bot noise, caps each text at an exact 12,000-character leading substring,
and targets a 350,000-character event block. Compaction provenance is part of the hashed
request. Rate-limit responses honor bounded server-directed retries.

Lookup order is:

1. `.cache/triage/<request-hash>.json`
2. `tests/fixtures/triage/<request-hash>.json`
3. Live API call when `OPENAI_API_KEY` is available
4. Loud cache-miss error when no exact replay and no key exist

A live response writes both cache locations atomically. Replays validate the stored request
by hashing it again and revalidate all evidence quotes against the current pre-firing input.

## Modes and persistence

All modes link to the immutable deterministic firing through `triage_results`:

| Mode | Agent | Feedback | Cause/culprit |
| --- | --- | --- | --- |
| `full` | GPT-5.6 | Active, bounded scorecard feedback | Structured result |
| `no_feedback` | GPT-5.6 | Disabled in the hashed prompt | Structured result |
| `no_agent` | None | None | Null |

Results also persist requested/returned model IDs, prompt hash, cache/replay flags,
`input_max_ts`, response ID, confidence, and exact evidence quotes. Scorecard feedback
submitted after triage captures the latest full-mode hypothesis and confidence.

## Verified webpack acceptance

Database: `.cache/webpack-v2.duckdb`

Firing: `firing:07cb50298e4a5badf1114403`

```bash
uv run sentinel triage firing:07cb50298e4a5badf1114403 \
  --db .cache/webpack-v2.duckdb --no-agent --json

uv run sentinel triage firing:07cb50298e4a5badf1114403 \
  --db .cache/webpack-v2.duckdb --no-feedback --json

uv run sentinel triage firing:07cb50298e4a5badf1114403 \
  --db .cache/webpack-v2.duckdb --json
```

Observed results:

| Mode | Prompt hash | Culprit | Confidence | Max input timestamp |
| --- | --- | --- | --- | --- |
| `no_agent` | none | none | none | none |
| `no_feedback` | `96686e532f18a193de07145eae97cc25cb4b78dc485fd64940c9663a3146e4fd` | `v5.99.0` | 0.99 | `2025-04-07T18:52:11Z` |
| `full` | `8a8e84a669ab09ef0f5b271a481a33cb9cbdda8bec5e9a7295613b534d6caf25` | `v5.99.0` | 0.99 | `2025-04-07T18:52:11Z` |

The v2 firing occurred at `2025-04-07T18:55:02Z`. The substantive maintainer
acknowledgment at `20:14:04Z`, including PR `#19398`, is absent from both exact requests.
Both GPT modes used five quotes copied from real pre-firing issue text. There was no active
feedback for this v2 metric, so the two results do not demonstrate a learning benefit.

## Deliberately next

The web surfaces remain for the next build slice. Detector v2 and its feedback snapshot
have been frozen, and the same holdout has been published beside v1. V2 emitted no
firings, so neither agent mode ran and no feedback improvement can be claimed.
