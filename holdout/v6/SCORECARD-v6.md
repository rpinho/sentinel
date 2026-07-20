# Sentinel v6 — rich-evidence two-stage result

> Frozen v4 candidate ranker followed by Claude Opus over rich, strictly pre-candidate evidence in scheduled-size batches.

| System | Precision | Hour recall | Incident recall | True alert-hours killed |
| --- | ---: | ---: | ---: | ---: |
| Stage 1 only | 13.6% | 88.0% | 88.2% | 0 |
| V5, one-event judge | 16.6% | 76.4% | 77.0% | 31 |
| V6, rich-evidence judge | 19.6% | 55.1% | 56.4% | 88 |

V6 estimates 749.667 surviving alerts and retains a median 12.318 hours of lead time.

## Clean-source cut

Completed bug closures + merged fixing PRs: 11.1% precision, 56.1% hour recall, 57.1% incident recall.

## Evidence, automation, and runtime

Median evidence: 10 events / 14,757 JSON characters per candidate. 20 subscription batches.

Automation-suspect candidates: 88; Stage 2 rejected 60 (68.2%).

Claude CLI modeled-equivalent usage: $32.544; no API-key or OpenAI calls.

## Limits

The full-label result is descriptive because the maintainer-comment source has 30% obvious audit noise. The clean-source cut removes that source. V6 reuses the published v4 test split and v5 sample, so this is not a fresh holdout.

Scorecard SHA-256: `fd1f49731874c08a721539952bfbe32103a7775e2d9850423c3ffdd731d97f2e`
