# Sentinel

Sentinel is an anomaly detector for public software-event streams that publishes its
misses and freezes every iteration.

**Headline:** a learned coherence ranker surfaces 88% of maintainer-confirmed incidents
a median 13.4 hours before confirmation. Repository-hour LLM judging failed; signal-
scoped v7 fixed the unit, but its election violated the committed funnel shape. V7.1
changes only that label-blind throttle; both frozen results remain visible.

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

Before publication, repository history was rewritten once to remove two lines of
personal prose from `HANDOFF.md`. No code, data, or hash-recorded artifact
bytes changed; frozen artifacts are preserved byte-for-byte. Commit ids recorded *inside*
those artifacts therefore refer to the pre-publication history. Anchor map (old → published):

- `928f681cd82c` → `2145bea74893`
- `a90f276e0cc6` → `cd4e67018ab9`
- `dca2f815b86b` → `5d968906efbe`
- `e2ee6717c6ab` → `85dbf4813c43`
- `fa96bee27a9e` → `82f4a85d8945`
