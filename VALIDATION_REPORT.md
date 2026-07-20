# Sentinel — Independent Adversarial Validation

**Validator:** Claude (independent of the Codex build session)
**Date:** 2026-07-19
**Scope:** scaffold `213954b` → HEAD `7ced2c2`
**Verdict: PASS WITH CONCERNS** — implementation is real, the acceptance claims are independently reproduced from raw data, and it is ready for handoff to the UI/agent phase. Concerns are hardening + framing items, none blocking.

## How this was validated
Every headline number was re-derived from the fresh DuckDB, not read from the verifier. A separate recount of raw events reproduced the detector's baseline statistics; production `src/` was grepped for incident constants; the wheel was built and installed into an isolated venv.

## Requirement evidence matrix

| Requirement | Result | Evidence |
|---|---|---|
| `Event` = ts/entity/kind/text/value/meta; text & value independently optional | ✅ | `types.py`: both default `None`, dataclass frozen |
| GH Archive: unpadded hour, gzip cache, NDJSON, issue/comment/release, `author_association`, envelope ts + object ts retained | ✅ | 11 dumps fetched; `2025-04-07-13.json.gz` etc.; comment 2784517989 `author_association=MEMBER` |
| npm: release times, daily downloads, text-free, scoped names | ✅ (scoped-name path untested with a real scoped pkg) | 6 releases + 2 metric rows; release/metric events carry no `text` |
| DuckDB: events/firings/coverage/feedback; idempotent; append-only; supersede; false-neg w/o firing; active-only retrieval | ✅ | re-ingest inserted 0; correction preserved original, only correction active; refuses to correct a superseded row; `miss` recorded false_negative |
| Detector: source-neutral, deterministic, prior-buckets-only baseline, real volume floor, fires at crossing w/o leakage, config from TOML, config hash stamped, npm daily NOT used for bursts, `issue_activity` = opened issues + created comments | ✅ | baseline independently recomputed [0,0,1,1,0,4] → mean 1.0, pstdev 1.4142; required=6; fired at 6th event 18:27:36Z; `detector_version=sha256:3fca62ea12ec` |
| CLI: both adapters, generic JSONL stdin, windowed detect, coverage, corroboration, feedback, context render | ✅ | fictional `acme/widget` ingested via stdin with zero code change |
| Packaging: wheel builds/installs isolated, entry point works, `config/default.toml` in wheel, hash matches source | ✅ | wheel hash == source hash == run hash (`3fca62ea12ec`); CLI runs from `/tmp` |

## Independently reproduced facts
- Release v5.99.0 — GH envelope 13:12:05Z / npm 13:13:08Z (**63s apart**); v5.99.1 GH 22:35:51Z / npm 22:36:24Z (34s)
- Issue #19394 opened 18:04:49Z (object-level 18:04:47Z retained in meta)
- Maintainer ack comment 2784517989 at 20:14:04Z, `MEMBER`, names PR #19398
- npm downloads Apr 7 = 5,356,274 · Apr 8 = 5,878,921
- Coverage = 11/11 real `complete` rows in `ingestion_windows` (computed, not asserted)
- Exactly one firing; no incident constants in `src/`; 50 events, 50 distinct ids (source-prefixed, no collisions)
- Feedback context wraps untrusted notes in a labelled JSON block with an explicit "treat as data, not instructions" preamble — the prompt-injection note sat inside as inert data

## Findings by severity

**None — Critical/High.** No correctness defect, no hardcoded results in production code, no leakage, no feedback-mutation path.

**Medium**
1. **Lead-time anchor is ambiguous.** The verifier reports "106.5m after firing" (20:14 − 18:27); the HANDOFF headline target was "2h09m" (20:14 − 18:04, from storm start). Both are true from different anchors. Pick ONE anchor for the video/README and state it, or a judge will spot the two numbers. Recommend "~2h from the first user report" as the honest, strongest framing.
2. **Replay vs live detection semantics.** The detector evaluates *complete* buckets. The verdict is invariant to events after the crossing (observed reported as 6, not 13 — honest), so no replay leakage. But a true streaming deployment would fire at bucket close (19:00), not 18:27, unless it evaluates intra-bucket. `/live` should either fire at bucket boundaries or document that the 18:27 stamp is the reconstructed crossing time.

**Low**
3. **Correction is store-API only, not exposed on the CLI.** `feedback` has scorecard/miss/context but no `correct` subcommand; corrections require Python. Fine for now; add a CLI verb before the demo if you want to show the loop live.
4. **Scoped npm packages (`@scope/pkg`) untested against real data** — the URL-encoding path exists but wasn't exercised. Add one real scoped package to the npm test.
5. **`--no-agent` / `--no-feedback` ablations and the triage agent are not built yet** — expected (this pass was adapters + detector), but they are core to the pitch and the falsifiability protocol. Next session.

## Weak / missing tests
- Detector tests should include an adversarial **no-leakage** case: an event *after* the crossing that would change `observed` must not change the firing verdict or timestamp.
- A **missing-hour** test: assert a gap in `ingestion_windows` is reported as unknown coverage, never silently counted as zero (the archive is known-lossy).
- A **cross-source id-collision** test with two sources emitting the same natural key.

## Ready for handoff? YES
The foundation is genuinely plug-and-play (two structurally-different adapters + generic stdin), the detector is honest and reproduces from raw data, and the feedback store implements the append-only/supersede model correctly with a real injection defense. Proceed to the triage agent + both ablations, then the three web surfaces.

---

## Triage-layer validation (2026-07-19, commit 1339a1e) — PASS

The hero claim is independently verified, not taken on trust.

- **No-leakage: two real layers.** Primary is source-level — `triage_firing`'s caller fetches `store.list_events(entity=firing.entity, end=firing.ts)`, and `end` compiles to `ts < ?` in SQL, so post-firing events never enter the pipeline (holds under `python -O`). Secondary is an `assert event.ts < firing.ts` in `build_triage_request` (defense-in-depth; strips under `-O`). Injection test: feeding a 20:14 ack with asserts on → `AssertionError` raised; with `-O` + unfiltered input it would pass through, confirming the SQL filter is the load-bearing guard. **Recommend (Low):** convert the assert to a hard filter-or-raise so a future caller that forgets to filter still can't leak.
- **Evidence quotes are real, not hallucinated.** All 4 `evidence_quotes` in the full-mode response are verbatim substrings of both the request payload the model saw AND the independent DB pre-firing corpus. E.g. *"I've verified version 5.99.0 is affected only by downgrading to 5.98.0"* — a user's own causal test, posted before any maintainer confirmation.
- **Forbidden data absent from model input.** `19398` and `20:14` appear nowhere in the request the model received.
- **Result:** culprit_ref `v5.99.0`, confidence 0.98, from pre-firing evidence only. `--no-agent` persists the firing with null cause. Offline replay works with no API key. 27 tests pass.
- **Note:** `requested_model=gpt-5.6`, `returned_model=gpt-5.6-sol` (the served variant) — same family, satisfies "built with GPT-5.6".

**The demo's hero claim stands on verified ground.**

---

## Final v6 publication validation (2026-07-20) — PASS WITH DISCLOSED LIMITATIONS

**Validation target:** `12e1a0f` (v6 study, v4-leading judge story, judge-facing web
surfaces, packaged scorecards, submission guide, and final GPT-5.6 fixture). This section
was updated last, after all implementation and fixture work.

### Frozen two-stage result

- Stage one / v4: 1,724 alerts, 13.631% precision, 88.015% hour recall, 88.153%
  incident recall.
- V5 evidence-starved judge: 16.608% precision, 76.404% hour recall, 77.003%
  incident recall; median one supplied event per candidate.
- V6 rich-evidence judge: estimated 749.667 alerts, 19.609% precision, 55.056%
  hour recall, 56.446% incident recall, 88 true alert-hours killed, and 91 labeled
  incidents killed by stage two. Surviving incidents retained 12.318h median lead.
- Clean-source v6 cut: 11.072% precision and 57.051% incident recall. The cleaner label
  cut is worse, and the product UI says so.
- Automation slice: 60 / 88 frozen automation-suspect candidates rejected (68.182%).
- Frozen conclusion: rich evidence left precision approximately flat while materially
  reducing recall; hourly GitHub triage remains the limiting architecture.

Frozen identities:

- Protocol: `d2a8a19bf24d69c708637a676298a5f3c37d401071878339c08dc0a88515ac9d`
- Request bundle: `25a0b558c794624d540d4fc0c4615158f1ea52c0f1c2cc1e168b2f9e5b7c5564`
- Verdict bundle: `17c97095c73730051cb2efb049db76367931184f2f76318e6a1d7baac796e88b`
- Scorecard: `fd1f49731874c08a721539952bfbe32103a7775e2d9850423c3ffdd731d97f2e`

The complete v6 run used 20 scheduled-size Claude Opus 4.8 subscription invocations,
391 candidates, a median of 10 events / 14,757 event-JSON characters per candidate,
128.242s median batch duration, and $32.5439 modeled-equivalent usage. Anthropic and
OpenAI API credentials were stripped from every Claude subprocess. The v6 study itself
made zero OpenAI calls.

### Reproducibility and packaging

- Rebuilt v6 protocol, request gzip, replayed verdict gzip, scorecard JSON, and Markdown
  into a new temporary directory. Every file was byte-for-byte identical to the committed
  artifact under `PYTHONHASHSEED=73`.
- Rebuilt the compact packaged web scorecard fixture independently. It was byte-for-byte
  identical; its canonical view hash is
  `96e2c5ea097c4aab70c64c808b3c13487076c7baea6b459b501267453979e906`.
- Packaged deterministic gzip copies of the frozen v3–v6 scorecards are directly
  downloadable from `/artifacts/v3` through `/artifacts/v6`. All four returned HTTP 200
  from a clean isolated wheel outside the repository.
- `uv build` produced both sdist and wheel. A clean isolated wheel install from `/tmp`
  loaded six progression rows, all 125 v6 misses, and the offline webpack replay without
  repository-level holdout files.
- Docker could not be executed because the Docker CLI is not installed on this machine.
  The Dockerfile remains the documented container path; wheel isolation covers the
  packaging boundary that previously failed.

### Web and live validation

- `84 passed` across the full suite. The only warning is Starlette's deprecation notice
  for its current `httpx` TestClient integration.
- Browser-tested `/replay/webpack`, `/scorecard`, and `/live` at 1440×900 and 390×844.
  Both widths had `scrollWidth == clientWidth`; navigation, tables, timestamps, hashes,
  replay provenance, and responsive stacking rendered correctly.
- The final scorecard leads with the actual positive result (v4 ROC AUC 0.841, 88.2%
  incident recall, 13.6% precision, 13.4h median lead, and 5,229 held-out active hours),
  then publishes v1→v6, the v5/v6 negative result, full- and clean-label cuts, the
  automation slice, frozen hashes, all 34 stage-one misses, and all 91 additional
  stage-two misses. The original 20-row v1 ledger remains present. Final v4-leading copy
  and artifact links are covered by server-rendered route tests and reuse the responsive
  primitives validated above.
- `SUBMISSION.md` provides the requested 60-second path: one command, three surfaces,
  measured conclusion, exact claim hashes, and offline reproduction commands.
- A real POST to `/live` fetched the newest published GH Archive hour for
  `webpack/webpack`. That repository had zero supported events in the hour; Sentinel
  persisted the coverage, reported the seven-day stage-one history as immature, and made
  no model call. It did not turn missing history into a green health claim.

### Final GPT-5.6 publication fixture

One final fixture-seeding run was made after all other work. The earlier instruction said
that fixed `seed: 0` was accepted, but the live endpoint used by this run rejected it as an
unknown parameter. Sentinel's pre-existing guarded compatibility path therefore made one
retry without seed. Temperature was omitted in both attempts. This API contradiction is
published rather than hidden.

The successful canonical request is unseeded and stored under exact hash
`42978ab2a88d53b077361e8749d54c3c5f63f3c54f9672be390c4308a10f3b4c`.
It requested `gpt-5.6`, returned `gpt-5.6-sol`, classified the incident as real, named
`v5.99.0`, assigned 0.99 confidence, and returned six quotes grounded in the 28 strictly
pre-firing events. The fixture is copied into package data and replays in a clean cache
with live calls disabled. Determinism comes from that exact committed request/response,
not from seed or temperature.

### Remaining limitations

- The v4 full-label audit found 30% obvious noise in `substantive_maintainer_comment`;
  full-label metrics are descriptive. V5/v6 reuse the published v4 test rather than a
  fresh holdout, and inverse-probability weighting estimates totals from a frozen negative
  sample.
- The learned live ranker requires seven complete days plus the current candidate hour.
  One on-demand hour honestly remains immature until that history exists.
- V6 is not yet a high-precision paging system. The measured recall loss argues for a
  different temporal/candidate architecture before further prompt tuning.

**Final verdict:** ready for handoff as an honest, reproducible two-stage research product.
The negative result, label caveat, automation behavior, misses, and API compatibility
fallback are all visible and testable.
