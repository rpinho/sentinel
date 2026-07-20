# Sentinel v5 — two-stage held-out result

> Stage 1 is the unchanged v4 high-recall ranker. Stage 2 is a frozen Claude Opus precision filter over compact, strictly pre-candidate evidence.

## End-to-end result

| System | Alerts | Precision | Hour recall | Incident recall |
| --- | ---: | ---: | ---: | ---: |
| Stage 1 only | 1,724 | 13.6% | 88.0% | 88.2% |
| Stage 1 + Stage 2 | 1,228.333 estimated | 16.6% | 76.4% | 77.0% |

Stage 2 removed an estimated 495.667 of 1,724 candidates (28.8%). It killed **31** of Stage 1's 235 true alert-hours and **32** of its caught incidents.

Surviving incidents retain a median lead of 11.933 hours before confirmation.

## Clean-source cut

Against completed bug closures and merged fixing PRs only: **8.1% precision**, **67.6% hour recall**, and **68.6% incident recall**. This removes the noisy maintainer-comment label source from the positive class.

## Label-source recall

| Confirmation source | Incidents | Stage 1 recall | End-to-end recall | Stage 2 survival |
| --- | ---: | ---: | ---: | ---: |
| `completed_bug_closure` | 77 | 90.9% | 74.0% | 81.4% |
| `merged_fixing_pr` | 79 | 69.6% | 63.3% | 90.9% |
| `substantive_maintainer_comment` | 131 | 97.7% | 87.0% | 89.1% |

## Compact request and runtime envelope

The frozen study judged 391 candidates in **one agent invocation**: every true Stage 1 alert plus a deterministic inverse-probability-weighted sample of negative alerts. The batch was 524,327 characters total; each candidate supplied a median of 1 events. Full 24-hour source windows were hashed, never sent.

Claude CLI reported 1.744 USD of modeled equivalent usage. The complete batch finished in 669.582 seconds (five-minute target met: `false`). The run used claude.ai subscription auth; this is not an API billing claim, and no OpenAI calls were made.

## Interpretation limits

The full-label result is descriptive because the v4 held-out audit found 30% obvious noise in `substantive_maintainer_comment`. The clean-source cut uses the two reliable confirmation sources. V5 reuses the already-published v4 test split, so neither result is a fresh untouched-holdout claim.

Artifacts are frozen by scorecard SHA-256 `5c997bf587d82c801c70234f32a05516a965d2e8fa6260284d497b6235cadf80`.
