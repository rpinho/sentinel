Work in /Users/ricardo/projects/sentinel (a git repo with a working, independently-validated detector + adapters + feedback store). If your working directory is not that path, stop and tell me. Read VALIDATION_REPORT.md, HANDOFF.md, and the existing src/sentinel/ code before writing anything — reuse the existing SentinelStore, Event, Firing, DetectorConfig, and the feedback context that relevant_feedback() already renders. Do not re-scaffold and do not rewrite working modules.

This session builds the triage agent and BOTH ablations — the layer that turns a detected burst into a named cause, where GPT-5.6 (via the openai SDK, already a dependency) does what a threshold cannot. Commit as you go.

API KEY: the OpenAI key is in /Users/ricardo/projects/sentinel/.env as OPENAI_API_KEY (the file is gitignored). Load it via python-dotenv at the top of the triage module (add python-dotenv to dependencies), so the running process reads it from the file rather than needing it in the environment. There is $10 of prepaid credit on the account; the caching described below keeps spend near zero.

THE ONE REQUIREMENT THAT, IF WRONG, INVALIDATES THE WHOLE PROJECT — read first:
The entire pitch is "Sentinel named the culprit release BEFORE the maintainers confirmed the cause." Therefore the triage agent must NEVER see any event at or after the firing timestamp. The maintainer acknowledgment (comment 2784517989 at 2025-04-07T20:14:04Z, which literally names PR #19398) lands ~2 hours AFTER the firing at 18:27:36Z — if it leaks into the triage input, the agent is just reading the answer and the demo is a fraud. The triage context window for a firing MUST be strictly events with ts < firing.ts, scoped to that entity. Build this as a hard invariant with an assertion, and write a test that fails if a post-firing event ever reaches the agent. Non-negotiable; get it right first.

BUILD, in order:

1. src/sentinel/triage.py — a triage function that takes a Firing plus the pre-firing event window (ts < firing.ts, same entity) and returns structured JSON via GPT-5.6 using OpenAI structured-outputs / JSON-schema mode: {real: bool, cause: str, culprit_ref: str|null, confidence: float 0-1, evidence_quotes: string[]}. temperature 0 for determinism. The prompt gives the model the burst (counts vs baseline), the text of the issues/comments in the window, and any release/push events before the firing — and asks it to decide real breakage vs Hacktoberfest/HN-traffic/migration flood, name the most likely culprit release or commit, and quote the evidence it used. It MUST degrade gracefully when events carry no text at all (the npm adapter is text-free) — reason from timing and release proximity, say so, and lower confidence rather than erroring.

2. Response caching / fixtures — matters for three reasons: replay must recompute reproducibly, the demo must run without live billing, and credit is limited. Cache every LLM response keyed by a hash of the exact input (model + prompt + schema) under .cache/triage/, and make it committable as fixtures under tests/fixtures/triage/. If OPENAI_API_KEY is present, call live and write the cache; if absent, replay from cache/fixtures and label the run replayed. Never fabricate a response when there is no cache and no key — fail loudly.

3. The two ablations, wired through the CLI and the detect/triage path as REAL switches, not cosmetic:
   --no-agent : detector only, no triage. Baseline that answers "is the model load-bearing at all?" A firing with no triage still lands on the scorecard, without a cause/culprit.
   --no-feedback : run triage but do NOT inject the prior-verdicts feedback context. Answers "does learning from its own past misses improve it?" When feedback is ON, inject exactly what relevant_feedback()/the feedback context already renders (active-only, untrusted-data-wrapped) into the triage prompt.
   Persist which mode produced each triage result.

4. Persist triage output onto the firing/scorecard: cause, culprit_ref, confidence, evidence_quotes, the mode (full / no-agent / no-feedback), the model, and a cache-hit flag.

ACCEPTANCE CHECK to run on the validated webpack DB (.cache/claude-validation.duckdb, firing firing:9f151a8243fe964d47d56400):
- Triage on that firing, given ONLY pre-18:27:36Z events, names v5.99.0 (or the 5.99.0 release / the parser-refactor PR) as culprit, with evidence_quotes drawn from real issue text in the window — WITHOUT the 20:14 maintainer comment in its input. Print the input window's max ts to prove it is < firing ts.
- Show --no-agent: firing present, no cause.
- Show the same firing with and without --no-feedback so the two are comparable (feedback store may be empty; the switch must still change the assembled prompt — log the prompt hash under each mode).

Leave the web UI and the frozen-holdout precision run for next session. Note for later: the holdout must freeze BOTH the detector config hash AND a feedback-store snapshot, then run once with feedback and once with --no-feedback, publishing both numbers.

Standing instructions: tell me briefly about any design decision this brief doesn't cover, and if real data or the API contradicts the brief, stop and say so rather than working around it quietly. The credibility of this project is the whole product.
