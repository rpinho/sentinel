<!-- Session 1 prompt. Reconstructed from the build session — the verbatim original was a
transient file that predated the decision to log prompts. Content is faithful to what drove
commits b6f9375..7ced2c2 (Event schema, two adapters, detector, feedback store, CLI). -->

Work in /Users/ricardo/projects/sentinel — a git repo with a committed skeleton. If your working directory is not that path, stop and tell me. THIS IS GREENFIELD: no existing codebase, no scorecard, no dbt models. "The scorecard" and "the detector" are things you write from zero. Do not write design documents — write code, and commit as you go.

WHAT WE'RE BUILDING: Sentinel, an anomaly detection + triage agent for public event streams that grades itself in public. The scorecard is the product, not the alert. Submission to OpenAI's Build with Codex hackathon; built with Codex + GPT-5.6.

ARCHITECTURAL CONSTRAINT THAT MATTERS MOST: genuinely plug-and-play across data sources → TWO adapters from the first commit, not one plus a promise. GH Archive is bulk gzip NDJSON and text-rich; npm registry is keyed REST JSON and text-free. If the design only works when prose is present, it isn't generic — surface that on day one.

BUILD, in order:
1. src/sentinel/types.py — Event {ts, entity, kind, text, value, meta} where text AND value are both optional. kind ∈ issue|comment|release|metric.
2. src/sentinel/adapters/gharchive.py — fetch https://data.gharchive.org/YYYY-MM-DD-H.json.gz. Hour is NOT zero-padded; NDJSON not an array; ~80MB gz, cache to disk. Emit issue/comment/release; carry payload.comment.author_association into meta (maintainer-ack detection depends on it).
3. src/sentinel/adapters/npm.py — registry.npmjs.org/<pkg> for per-version publish times (text-free release events); api.npmjs.org/downloads/range/<from>:<to>/<pkg> for daily downloads (metric events with value). No auth. No free text — that's the point.
4. src/sentinel/store.py — DuckDB for events, firings, ingestion coverage, and an append-only feedback table (see FEEDBACK_LOOP.md). Corrections create superseding rows; false negatives need no firing.
5. src/sentinel/detect.py — per-entity burst detection against that entity's own trailing baseline, volume floor, deterministic. Config in a versioned TOML, stamped by hash onto every firing.
6. src/sentinel/cli.py — ingest from either adapter, detect over a window, accept generic-Event JSONL on stdin, report coverage, show cross-source release corroboration.

ACCEPTANCE: ingest webpack around 2025-04-07 from both adapters; detector fires on the issue storm opening 18:04:49Z (#19394). GH release v5.99.0 at 13:12:05Z; npm independently 13:13:08Z — surface the ~60s agreement. npm downloads are DAILY → severity + corroboration only, never burst detection.

STANDING INSTRUCTIONS: tell me briefly about design decisions the brief doesn't cover; and if real data contradicts the brief (a timestamp, a missing archive hour, an absent field), stop and say so rather than working around it quietly. The credibility of this project rests on the data being exactly what we claim.
