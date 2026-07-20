# Sentinel

Sentinel is an anomaly detector for public software-event streams that publishes its
misses and freezes every iteration.

**Headline:** a learned coherence ranker surfaces 88% of maintainer-confirmed incidents
a median 13.4 hours before confirmation. Adding an LLM triage stage at repository-hour
granularity does not work—we measured why.

## Run the judge path

```bash
uv sync --extra dev
uv run sentinel serve
```

Open:

- `http://127.0.0.1:8000/replay/webpack` — offline hero. Stage one fires at
  `18:55:02Z`; the committed GPT-5.6 fixture names `v5.99.0`; maintainers confirm at
  `20:14:04Z`, 79 minutes later. No key or network call is used.
- `http://127.0.0.1:8000/scorecard` — v1→v6, confidence intervals, clean-label cut,
  automation audit, frozen hashes, and all 125 v6 misses.
- `http://127.0.0.1:8000/live` — fetches a current GH Archive hour only after a button
  press. Missing history stays visibly immature; stage two degrades to candidates-only
  without Claude subscription auth.

## What was learned

Stage one is the positive result: held-out ROC AUC 0.841, 88.2% incident recall, 13.6%
precision, and 13.4h median lead. Sixty-four of 287 test incidents were outside old burst
windows entirely.

V5/v6 are the negative result. Rich evidence moved estimated precision from 16.6% to
19.6% but reduced incident recall from 77.0% to 56.4%. A repository-hour mixes unrelated
threads; the judge evaluates the bag instead of the incident. Future work changes the
triage unit to an issue thread, not the prompt.

## Frozen claim map

- v4 model: `57021d95ec13ef5d8c1f52f95e6008152b7c1a6ddccbe7fe5f77aa042a2a3f9d`
- v6 protocol: `d2a8a19bf24d69c708637a676298a5f3c37d401071878339c08dc0a88515ac9d`
- v6 verdicts: `17c97095c73730051cb2efb049db76367931184f2f76318e6a1d7baac796e88b`
- v6 scorecard: `fd1f49731874c08a721539952bfbe32103a7775e2d9850423c3ffdd731d97f2e`
- GPT-5.6 replay request: `42978ab2a88d53b077361e8749d54c3c5f63f3c54f9672be390c4308a10f3b4c`

```bash
uv run pytest -q
uv run python scripts/evaluate_detector_v4.py --model-commit a90f276e0cc6b886999506aa7e3b174afc3775aa
uv run python scripts/run_stage2_v6.py
uv run python scripts/evaluate_stage2_v6.py
```

Full caveats and validation evidence are in `VALIDATION_REPORT.md`.
