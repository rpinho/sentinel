# Sentinel

Sentinel is an anomaly detector for public software-event streams that publishes its
misses and freezes every iteration.

**Headline:** a learned coherence ranker surfaces 88% of maintainer-confirmed incidents
a median 13.4 hours before confirmation. Repository-hour LLM judging failed; signal-
scoped v7 fixed the unit, but its election violated the committed funnel shape. V7.1
changes only that label-blind throttle; both frozen results remain visible.

## Copy-ready Devpost fields

**Title:** Sentinel — the anomaly detector that publishes every miss

**Tagline:** A learned anomaly ranker with a public, replayable scorecard.

**Recommended track:** **Developer Tools.** Sentinel monitors software-event streams and
turns them into testable anomaly candidates, deterministic evidence packs, agent verdicts,
and a public miss ledger. That maps directly to the track's testing, DevOps, and agentic-
workflow language. Work & Productivity is defensible because the output helps teams triage
faster, but it describes the beneficiary rather than the product; Developer Tools is the
closer category. Ricardo makes the final selection.

**Thumbnail:** upload `src/sentinel/web/static/og.png`.

**Technologies:** Python, FastAPI, DuckDB, scikit-learn, Jinja2, uv, GPT-5.6, OpenAI
Responses API, Claude Code, GH Archive, npm, Docker, Render.

**Repository:** https://github.com/rpinho/sentinel

**Hosted demo:** `[paste the onrender.com URL after deployment]`

**Public YouTube demo:** `[paste the public video URL]`

**Primary Codex thread:** `019f7bbe-1df8-7a61-b236-51a38404e1b2`. In that thread, run
`/feedback`, choose to share the existing session, and submit it. Use the session ID the
dialog returns in the form if it differs from the technical thread ID shown here.

### Description / project story

Sentinel surfaced **253 of 287 maintainer-confirmed incidents (88.2%) a median 13.4
hours before confirmation** on a whole-repository-held-out study of public GitHub events.
Its learned coherence ranker reached ROC AUC 0.841 and found 64 incidents that were
invisible to the old burst gate. It publishes the uncomfortable parts beside the result:
13.6% alert precision, a label-source audit that exceeded its noise ceiling, and every
known miss.

The product ingests public software-event streams into a source-neutral contract, scores
active repository-hours with learned coherence features, turns alerts into signal-scoped
claims, fetches bounded evidence only for elected claims, and routes structured findings.
The judge path is a real, no-key web application: `/replay/webpack` recomputes a detector
firing from 245 source events and resolves the exact committed GPT-5.6 response;
`/scorecard` publishes the complete v1→v7.1 experiment trail and miss ledgers; `/live`
fetches a recent GH Archive hour on demand and reports incomplete history as immature.

The most important result is methodological. Repository-hour LLM triage did not work:
richer evidence improved estimated precision only from 16.6% to 19.6% while reducing
incident recall from 77.0% to 56.4%. V7 corrected the candidate unit but accidentally
compressed 4,412 screened claims to four investigations, violating a predeclared funnel
shape. V7.1 froze every other input and changed only that label-blind throttle to 442.
It caught six additional incidents, but story precision fell to 24.2%. Both runs remain
published. Sentinel learns through explicit, falsifiable iterations instead of silently
tuning away a negative result.

Codex implemented and validated the project from scaffold to deployment: adapters,
DuckDB and feedback storage, deterministic detectors, temporal no-leakage boundaries,
provider-independent triage, exact-response caching, frozen studies, scorecard surfaces,
responsive browser checks, and release packaging. Human decisions defined the labels,
freeze boundaries, candidate unit, funnel contract, and publish-every-miss rule. GPT-5.6
powers the offline hero: using only 28 strictly pre-firing events, it names webpack
`v5.99.0` 79 minutes before the maintainer confirmation. The committed request/response
cache—not temperature or seed—is the source of deterministic replay.

### What is new during Build Week

The repository's dated commit history and primary Codex session record the Build Week
work: the initial event-stream scaffold; feedback-aware agent triage; powered v2-v4
detector studies; v5-v7.1 precision-stage experiments; exact GPT-5.6 replay; public web
surfaces; responsive validation; packaging; and hosted-deployment preparation. The frozen
artifact hashes make the sequence auditable even where publication-time commit IDs were
remapped.

## Deploy the judge instance on Render

No API key, database, payment plan, or application secret is needed for the replay and
scorecard. The only account-gated step is approving the free Render Blueprint:

1. Make sure the final `main` commit is visible at https://github.com/rpinho/sentinel.
2. Open the **Deploy to Render** button in `README.md`, or go directly to
   `https://render.com/deploy?repo=https://github.com/rpinho/sentinel`.
3. Sign in to Render. On the Blueprint review, keep the repository's default branch and
   confirm one `sentinel-detect` Web Service using **Docker** and the **Free** plan.
4. Click **Deploy Blueprint**. No environment variables are required.
5. Open the new service and wait for the Events page to report the deploy is live. Click
   its `onrender.com` URL; the root redirects to `/replay/webpack`.
6. Verify `/replay/webpack` and `/scorecard`, then paste the base URL into Devpost's
   website/testing field. If the service name was already taken, use the generated URL
   Render displays rather than assuming the subdomain.

The Blueprint disables automatic deploys so the judged commit stays stable. If it was
created before the final commit, use **Manual Deploy → Deploy latest commit** once. Render
free Web Services spin down after 15 idle minutes and can take about a minute to wake;
that loading delay does not require credentials or payment from judges.

## One-take demo video — 2:45 target

Before recording, open the hosted `/scorecard` at the top, the hosted `/replay/webpack`,
and the GitHub README at **How Codex was used** in three tabs. Use voice only—no music—and
hide bookmarks, notifications, account avatars, and unrelated tabs.

| Time | Screen and action | Voiceover |
|---|---|---|
| 0:00–0:22 | Start on `/scorecard`; hold on the five headline tiles. | “Sentinel is an anomaly detector that grades itself in public. On held-out public software events, its learned ranker surfaced 88% of maintainer-confirmed incidents a median 13.4 hours early. It also publishes 13.6% precision, its label caveat, and every miss.” |
| 0:22–1:05 | Switch to `/replay/webpack`; point to source count, firing time, GPT-5.6 verdict, then maintainer confirmation. | “This replay is computed from 245 committed source events, not a screenshot. Stage one fires at 18:55. GPT-5.6 sees only the 28 earlier events, names version 5.99.0, and the exact cached response replays with network calls disabled. The maintainer confirmation arrives 79 minutes later.” |
| 1:05–1:43 | Return to `/scorecard`; show v4 and scroll into the miss ledger. | “The positive result is the learned coherence ranker: point-eight-four-one AUC, 88% incident recall, and 64 incidents outside the old burst population. The audit also found one noisy label source, so these metrics are descriptive. The scorecard exposes that caveat and all labeled misses instead of hiding them.” |
| 1:43–2:20 | Scroll through the v1→v7.1 progression and paired v7 funnels. | “Each failure became one frozen intervention. V2 killed noise and signal. Richer hourly LLM evidence still lost recall. V7 fixed the candidate unit but compressed 4,412 claims to four, violating the design. V7.1 changed only that throttle to 442 and published the lower 24.2% precision and 2.44% recall it actually measured.” |
| 2:20–2:45 | Switch to README **How Codex was used**; end on repo/deploy links. | “Codex built and validated the adapters, detector, leakage boundaries, frozen studies, exact caches, and web surfaces. Human decisions set the labels, freeze rules, and publish-every-miss contract. GPT-5.6 powers the grounded hero verdict. The code, hashes, hosted demo, and full experiment trail are public.” |

Upload the recording as a public YouTube video, keep it under three minutes, and paste its
URL into the submission. Do not add copyrighted music or unrelated third-party imagery.

## Run the judge path

```bash
uv sync --extra dev
uv run sentinel serve
```

Open:

- `http://127.0.0.1:8000/replay/webpack` — offline hero. Stage one fires at
  `18:55:02Z`; the committed GPT-5.6 fixture names `v5.99.0`; maintainers confirm at
  `20:14:04Z`, 79 minutes later. No key or network call is used.
- `http://127.0.0.1:8000/scorecard` — v1→v7.1, paired signal-scoped funnels, confidence
  intervals, clean-label cuts, automation accounting, frozen hashes, and the v6 miss ledger.
- `http://127.0.0.1:8000/live` — fetches a current GH Archive hour only after a button
  press. Missing history stays visibly immature; stage two degrades to candidates-only
  without Claude subscription auth.

## What was learned

Stage one is the positive result: held-out ROC AUC 0.841, 88.2% incident recall, 13.6%
precision, and 13.4h median lead. Sixty-four of 287 test incidents were outside old burst
windows entirely.

V5/v6 are the first negative result. Rich evidence moved estimated precision from 16.6% to
19.6% but reduced incident recall from 77.0% to 56.4%. A repository-hour mixes unrelated
threads; the judge evaluates the bag instead of the incident.

V7 isolated a policy-spec failure rather than another architectural failure. It emitted
16,221 signal-scoped claims, deterministically gated to 4,412, and screened them all in
23 scheduled-size Opus subscription calls. The 4,412→4 election was 1,103× compression,
violating the pre-declared roughly-order-of-magnitude funnel shape before labels were
consulted. V7.1 freezes everything else and elects the label-blind top 442. It publishes
33 stories, catches 7 / 287 incidents, and measures 24.2% story precision and 2.44%
incident recall. Whatever the corrected run said was published beside v7 without tuning.

## Frozen claim map

- v4 model: `57021d95ec13ef5d8c1f52f95e6008152b7c1a6ddccbe7fe5f77aa042a2a3f9d`
- v6 protocol: `d2a8a19bf24d69c708637a676298a5f3c37d401071878339c08dc0a88515ac9d`
- v6 verdicts: `17c97095c73730051cb2efb049db76367931184f2f76318e6a1d7baac796e88b`
- v6 scorecard: `fd1f49731874c08a721539952bfbe32103a7775e2d9850423c3ffdd731d97f2e`
- v7 protocol: `dd5cf502cc62108d82792690be0d19a961e85307063c251f7e3c439e0f5f06e9`
- v7 screen requests: `ea880012f9c4a23cabfce53bc1020a832e8cb7085d2d3fbeb8fd41fa74b97082`
- v7 screen verdicts: `05e486df639c302ede86e5ef7fc63960de468109e82863b4eeccd530866dc839`
- v7 investigation requests: `9e3384794c4be092fb81443e21b89f4a595766929967515e75d045269f81b611`
- v7 investigation verdicts: `1044b88d93159b66099494199b52bb49d4e0f7248e1f725d5474814af7c3d90e`
- v7 scorecard: `c2097f6ed1039f4295b1d26db2c71cc24472a0a6d98bda68fe68959e0578e19f`
- v7.1 protocol: `bd7336d99fc9603c0ecb6913adb4159b64271484e0dfb3025e938c82973040f9`
- v7.1 election manifest: `759b84ce4c20ace8aeb8d589df13ef50f24306d0f6ae8b926ba019aa5f39bcd8`
- v7.1 investigation requests: `f773dabdb37e76b5b078ee975cea323a764b9fa2b314e5424f42657d7f9fc547`
- v7.1 investigation verdicts: `93a0ce8ba15f93706e32e95327b00500e04c23c4a0949e9fc1229e16ce716a6d`
- v7.1 scorecard: `1bba31c415856ff0eedfe6d68efccf36c6436e7e525f9af4d4302fd857083af5`
- GPT-5.6 replay request: `42978ab2a88d53b077361e8749d54c3c5f63f3c54f9672be390c4308a10f3b4c`

```bash
uv run pytest -q
uv run python scripts/evaluate_detector_v4.py --model-commit a90f276e0cc6b886999506aa7e3b174afc3775aa
uv run python scripts/run_stage2_v6.py
uv run python scripts/evaluate_stage2_v6.py
uv run python scripts/run_stage2_v7.py --phase screen
uv run python scripts/build_v7_investigations.py
uv run python scripts/run_stage2_v7.py --phase investigation
uv run python scripts/evaluate_stage2_v7.py
uv run python scripts/build_v71_protocol.py
uv run python scripts/build_v71_investigations.py
uv run python scripts/run_stage2_v7.py --phase investigation \
  --protocol holdout/v7.1/protocol.json \
  --requests holdout/v7.1/requests-investigation.json.gz \
  --cache-dir .cache/v7.1/claude-cli-investigation \
  --output holdout/v7.1/verdicts-investigation.json.gz
uv run python scripts/evaluate_stage2_v71.py
```

Full caveats and validation evidence are in `VALIDATION_REPORT.md`.

## Note on commit ids inside frozen artifacts

Before publication, repository history was rewritten to remove private, non-project
narrative and standardize publication metadata. No frozen code result, dataset, or
hash-recorded artifact bytes changed. Commit ids recorded *inside* those artifacts
therefore refer to the pre-publication history. Anchor map (old → published):

- `928f681cd82c` → `2145bea74893`
- `a90f276e0cc6` → `cd4e67018ab9`
- `dca2f815b86b` → `5d968906efbe`
- `e2ee6717c6ab` → `85dbf4813c43`
- `fa96bee27a9e` → `82f4a85d8945`
