# Sentinel frozen holdout

> Generated directly by `scripts/run_holdout.py`; results are unedited.

## Frozen inputs

- Window: `2025-05-12T00:00:00Z` to `2025-05-19T00:00:00Z`
- Repositories: `facebook/react`, `vercel/next.js`, `microsoft/vscode`
- Frozen commit: `e2ee6717c6ab9a43126452f9cc1c5a796be8454a`
- Execution commit: `fa96bee27a9e639ccf9bfcf19fa8363aaaf1d470`
- Detector config: `3b11a572823b54f9ea5a0801df2becef809d5e092962ded5bac556b2d1845106`
- Detector version: `sha256:3b11a572823b`
- Feedback snapshot: `c00c486998a8bc82d0bd08780d209f766bfb7528a8d07cf8399ec151d33a0736`
- Selection policy: First full Monday five weeks after the webpack demo; three predeclared high-traffic JavaScript repositories, selected before archive inspection.

## Raw metrics

| Mode | Firings | TP | FP | Misses | Unresolved | Precision | Median lead | FP/week |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `no_agent` | 0 | 0 | 0 | 0 | 0 | N/A | N/A | 0.00 |
| `no_feedback` | 0 | 0 | 0 | 0 | 0 | N/A | N/A | 0.00 |
| `full` | 0 | 0 | 0 | 0 | 0 | N/A | N/A | 0.00 |

Feedback precision delta (full − no-feedback): `N/A`.

Archive coverage: `504/504` repo-hours (100.0%).

Detector interval verdicts: `{"automation_suppressed": 7, "candidate": 23, "immature": 684, "insufficient_exposure": 594, "observe_only": 6, "quiet": 702}`.

Verdicts use the committed deterministic maintainer-acknowledgment rule. No later acknowledgment means `UNRESOLVED`, not a guessed false positive.

### Methodology amendment

The first raw runs exposed label leakage: generated PR statistics matched acknowledgment phrases (including CI posted through a human maintainer account), issue activity later in the firing hour could label an earlier firing, and an explicit downstream-distributor closure was read as a fix. V3 grades only issue acknowledgments, excludes bots, restricts candidate issue activity to timestamps at or before the firing, and recognizes the disclosed downstream-build negative phrase. Detector outputs and agent fixtures were unchanged.

## Every firing

| Fired at | Entity | Full hypothesis | Culprit | Confidence | Verdict | Ack |
| --- | --- | --- | --- | ---: | --- | --- |

## Published detector misses

| Reported at | Entity | Issue | Maintainer acknowledgment |
| --- | --- | --- | --- |
| `2025-05-12T14:24:21Z` | `microsoft/vscode` | https://github.com/microsoft/vscode/issues/248732 | https://github.com/microsoft/vscode/issues/248732#issuecomment-2875102699 |
| `2025-05-13T07:00:45Z` | `microsoft/vscode` | https://github.com/microsoft/vscode/issues/248784 | https://github.com/microsoft/vscode/issues/248784#issuecomment-2883165802 |
| `2025-05-16T15:10:06Z` | `microsoft/vscode` | https://github.com/microsoft/vscode/issues/249108 | https://github.com/microsoft/vscode/issues/249108#issuecomment-2887134540 |
| `2025-05-17T20:47:19Z` | `microsoft/vscode` | https://github.com/microsoft/vscode/issues/249187 | https://github.com/microsoft/vscode/issues/249187#issuecomment-2888594719 |

## Interpretation

This is a frozen holdout, not a tuned benchmark. `UNRESOLVED` rows remain visible. Feedback is judged only by the measured full versus no-feedback difference above; no improvement is claimed when the delta is zero or unknown.
