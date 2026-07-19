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
The live endpoint rejects `temperature: 0`. During holdout seeding it also rejected a
fixed seed passed in the Responses request body with HTTP 400, `Unknown parameter: 'seed'`.
Neither field is sent. The source of truth for reproducibility is the SHA-256 of the exact
effective request plus its committed response fixture.

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

Database: `.cache/claude-validation.duckdb`

Firing: `firing:9f151a8243fe964d47d56400`

```bash
uv run sentinel triage firing:9f151a8243fe964d47d56400 \
  --db .cache/claude-validation.duckdb --no-agent --json

uv run sentinel triage firing:9f151a8243fe964d47d56400 \
  --db .cache/claude-validation.duckdb --no-feedback --json

uv run sentinel triage firing:9f151a8243fe964d47d56400 \
  --db .cache/claude-validation.duckdb --json
```

Observed results:

| Mode | Prompt hash | Culprit | Confidence | Max input timestamp |
| --- | --- | --- | --- | --- |
| `no_agent` | none | none | none | none |
| `no_feedback` | `4a1a32397966ece3416134eec926a485afa869c0d57d17c61e3c2f00386837f6` | `v5.99.0` | 0.98 | `2025-04-07T18:27:04Z` |
| `full` | `d70b11c9db7e918b945460220942cbccfc86657a8df830710f1953ab5df247f6` | `v5.99.0` | 0.98 | `2025-04-07T18:27:04Z` |

The firing occurred at `2025-04-07T18:27:36Z`. The substantive maintainer acknowledgment
at `20:14:04Z`, including PR `#19398`, is absent from both exact requests. Both GPT modes
used four quotes copied from real pre-firing issue text.

## Deliberately next

The web scorecard/replay UI and the frozen-holdout precision run remain for the next build
slice. The holdout must freeze both the detector config hash and a feedback-store snapshot,
then run once in full mode and once with `--no-feedback`, publishing both outcomes.
