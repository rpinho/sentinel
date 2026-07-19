# Sentinel frozen holdout

> Generated directly by `scripts/run_holdout.py`; results are unedited.

## Frozen inputs

- Window: `2025-05-12T00:00:00Z` to `2025-05-19T00:00:00Z`
- Repositories: `facebook/react`, `vercel/next.js`, `microsoft/vscode`
- Frozen commit: `928f681cd82c91194f5d3aae746ca08c05c90287`
- Execution commit: `dca2f815b86b0b4da893290e2dc5b644c601b6f3`
- Detector config: `3fca62ea12ec74d0c406543e54d3a60772606fae6e13b17e756b78ead0323332`
- Detector version: `sha256:3fca62ea12ec`
- Feedback snapshot: `062c596a46c6592c839716bdba2f7bdb29ef57401c50cc8f11f2e861f326fe4f`
- Selection policy: First full Monday five weeks after the webpack demo; three predeclared high-traffic JavaScript repositories, selected before archive inspection.

## Raw metrics

| Mode | Firings | TP | FP | Misses | Unresolved | Precision | Median lead | FP/week |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `no_agent` | 20 | 1 | 1 | 0 | 18 | 50.0% | 414.4 min | 1.00 |
| `no_feedback` | 20 | 0 | 0 | 1 | 18 | N/A | N/A | 0.00 |
| `full` | 20 | 0 | 0 | 1 | 18 | N/A | N/A | 0.00 |

Feedback precision delta (full − no-feedback): `N/A`.

Archive coverage: `504/504` repo-hours (100.0%).

Verdicts use the committed deterministic maintainer-acknowledgment rule. No later acknowledgment means `UNRESOLVED`, not a guessed false positive.

### Methodology amendment

The first raw runs exposed label leakage: generated PR statistics matched acknowledgment phrases (including CI posted through a human maintainer account), issue activity later in the firing hour could label an earlier firing, and an explicit downstream-distributor closure was read as a fix. V3 grades only issue acknowledgments, excludes bots, restricts candidate issue activity to timestamps at or before the firing, and recognizes the disclosed downstream-build negative phrase. Detector outputs and agent fixtures were unchanged.

## Every firing

| Fired at | Entity | Full hypothesis | Culprit | Confidence | Verdict | Ack |
| --- | --- | --- | --- | ---: | --- | --- |
| `2025-05-14T17:19:13Z` | `facebook/react` | The five-event hourly burst is heterogeneous routine repository activity, not a coordinated breakage signal: it includes an automated bundle-size report, a report attributed to Chrome rather than React, a status update on an older issue, and a copied/derived issue post. No common new release, commit, or failure mode connects the events. | `none` | 0.98 | **UNRESOLVED** | none |
| `2025-05-12T07:54:54Z` | `microsoft/vscode` | The burst is a heterogeneous mix of routine maintainer triage, bot responses, feature/PR discussion, issue closures, and unrelated reports. There is no concentrated symptom or shared release indicating a breakage incident. | `none` | 0.97 | **UNRESOLVED** | none |
| `2025-05-13T05:51:09Z` | `microsoft/vscode` | The threshold crossing is best explained by a clustered maintainer-triage session plus unrelated user activity. Several near-consecutive comments route separate Copilot reports to the same existing issue/workaround, while other events concern tooltips, terminal rendering, NES, and general stuttering; there is no coherent new failure dominating the burst. | `none` | 0.93 | **UNRESOLVED** | none |
| `2025-05-13T13:56:05Z` | `microsoft/vscode` | The burst is a heterogeneous mix of routine maintainer triage, support replies, design discussion, and issue closure across unrelated topics. It does not show a concentrated set of reports for one failure mode or release, so no causal culprit is supported. | `none` | 0.95 | **UNRESOLVED** | none |
| `2025-05-14T00:52:35Z` | `microsoft/vscode` | Expected issue-triage and discussion activity, not a coherent breakage incident. The firing-hour events span unrelated topics and include multiple routine bot closures, routing responses, and version reminders; no common failure mode or release-linked cluster is evident. | `none` | 0.97 | **UNRESOLVED** | none |
| `2025-05-15T07:53:42Z` | `microsoft/vscode` | The marginal burst is heterogeneous routine repository activity—unrelated feature requests, configuration troubleshooting, bot triage, and an upstream closure—not a coherent cluster of reports describing one breakage. | `none` | 0.92 | **TRUE** | https://github.com/microsoft/vscode/issues/246293#issuecomment-2884101665 |
| `2025-05-16T06:57:07Z` | `microsoft/vscode` | The burst is a heterogeneous mix of routine feature requests, troubleshooting comments, and unrelated individual bugs rather than a coordinated flood around one failure. One existing webview thread is explicitly called a regression, but the surrounding activity does not share that symptom or a common release cause. | `none` | 0.93 | **FALSE** | https://github.com/microsoft/vscode/issues/248597#issuecomment-2886027023 |
| `2025-05-16T09:58:34Z` | `microsoft/vscode` | The threshold crossing is best explained by concentrated maintainer triage of a backlog of extension-signature reports, including paired issue closures and near-identical comments. The reports concern mandatory signature verification in unofficial Code OSS distributions, not a newly emerging VS Code-wide failure at the firing time. | `none` | 0.93 | **UNRESOLVED** | none |
| `2025-05-17T11:25:59Z` | `microsoft/vscode` | Routine automation: the activity spike was driven by vs-code-engineering[bot] batch-closing stale issues labeled info-needed and posting matching closure comments immediately before the cutoff, not by a new breakage incident. | `none` | 0.99 | **UNRESOLVED** | none |
| `2025-05-18T02:55:09Z` | `microsoft/vscode` | Expected routine automation, not a breakage incident. The end-of-window spike is dominated by near-simultaneous VS Code lifecycle-bot reminders on unrelated feature requests, with identical text and a shared “10 days to go” schedule marker. | `none` | 0.99 | **UNRESOLVED** | none |
| `2025-05-12T07:35:01Z` | `vercel/next.js` | Expected CI/PR automation flood: four of the six events in the firing hour were generated build-stat or failing-test reports on PRs #79071 and #79072, alongside one maintainer discussion and one routine issue closure. The failures concern different tests and commits, so they do not indicate a coherent breakage incident. | `none` | 0.98 | **UNRESOLVED** | none |
| `2025-05-13T19:50:13Z` | `vercel/next.js` | Expected automation flood: changeset-bot posted near-identical “No Changeset found” comments across multiple release-related PRs within seconds, accounting for much of the activity spike. Other nearby events are routine CI/test and merge notifications, not a coherent user-facing breakage cluster. | `none` | 0.99 | **UNRESOLVED** | none |
| `2025-05-14T07:17:54Z` | `vercel/next.js` | Expected CI/bot activity around routine generated Rspack/Turbopack manifest-update pull requests, including changeset notices, build-stat reports, and a test result—not a coherent user-facing breakage burst. | `none` | 0.98 | **UNRESOLVED** | none |
| `2025-05-15T00:05:51Z` | `vercel/next.js` | Expected routine automation: the burst is dominated by GitHub Actions posting identical inactivity-lock comments on multiple closed issues within seconds, not reports of a shared breakage. | `none` | 0.99 | **UNRESOLVED** | none |
| `2025-05-15T07:14:16Z` | `vercel/next.js` | Expected routine automation flood: two manifest-update PRs (#79232 and #79233) triggered changeset, CI-failure, and build-stat comments in quick succession, alongside a few unrelated user comments. The events do not form a coherent breakage incident. | `none` | 0.97 | **UNRESOLVED** | none |
| `2025-05-16T05:43:44Z` | `vercel/next.js` | Expected automation flood, dominated by repeated CI failure reports on PR #79260 plus changeset/Graphite and invalid-reproduction bot activity; the events do not form a coherent user-facing breakage incident. | `none` | 0.99 | **UNRESOLVED** | none |
| `2025-05-16T16:57:42Z` | `vercel/next.js` | Expected repository automation flood, dominated by changeset-bot and CI/statistics comments on a cluster of newly opened pull requests rather than correlated user reports of one breakage. | `none` | 0.97 | **UNRESOLVED** | none |
| `2025-05-17T07:09:00Z` | `vercel/next.js` | Expected routine automation flood: changeset-bot posted near-simultaneous comments on newly opened Rspack and Turbopack test-manifest update PRs, accounting for most of the five-event hourly burst. The activity does not show a shared user-facing failure or release regression. | `none` | 0.99 | **UNRESOLVED** | none |
| `2025-05-18T07:07:37Z` | `vercel/next.js` | Expected routine automation: multiple generated Rspack/Turbopack test-manifest PRs were opened together, each eliciting the same changeset-bot comment. This clustered bot activity, not a breakage incident, explains the burst. | `none` | 0.99 | **UNRESOLVED** | none |
| `2025-05-18T16:38:46Z` | `vercel/next.js` | Expected routine PR/CI automation, not a user-reported breakage incident. The firing-hour activity is dominated by changeset-bot and automated build-stat/test-failure comments on PRs #79350 and #79351; the reported ReactRefresh test mismatch also appeared on unrelated PRs earlier in the window, suggesting shared CI/baseline noise rather than a new release regression. | `none` | 0.97 | **UNRESOLVED** | none |

## Published detector misses

| Reported at | Entity | Issue | Maintainer acknowledgment |
| --- | --- | --- | --- |
| `2025-05-13T07:00:45Z` | `microsoft/vscode` | https://github.com/microsoft/vscode/issues/248784 | https://github.com/microsoft/vscode/issues/248784#issuecomment-2883165802 |
| `2025-05-16T15:10:06Z` | `microsoft/vscode` | https://github.com/microsoft/vscode/issues/249108 | https://github.com/microsoft/vscode/issues/249108#issuecomment-2887134540 |
| `2025-05-17T20:47:19Z` | `microsoft/vscode` | https://github.com/microsoft/vscode/issues/249187 | https://github.com/microsoft/vscode/issues/249187#issuecomment-2888594719 |

## Interpretation

This is a frozen holdout, not a tuned benchmark. `UNRESOLVED` rows remain visible. Feedback is judged only by the measured full versus no-feedback difference above; no improvement is claimed when the delta is zero or unknown.
