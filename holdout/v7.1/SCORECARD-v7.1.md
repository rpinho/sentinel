# Sentinel v7.1 election-corrected scorecard

V7.1 freezes every v7 component except the election policy. It reuses the
exact 4,412 v7 screen verdicts and investigates the label-blind top decile.

## Result

- Published stories: **33**
- Story precision: **24.2%**
- Hour-mapped precision: **22.6%**
- Incident recall: **2.4%**
- Incidents caught: **7 / 287**
- True stage-one alert-hours killed: **228**
- Median lead: **10.0 hours**
- Clean-source story precision / incident recall: **15.2% / 2.6%**

| Version | Hour precision | Incident recall | True hours killed | Median lead |
|---|---:|---:|---:|---:|
| v5 | 16.6% | 77.0% | 31 | 11.9 h |
| v6 | 19.6% | 56.4% | 88 | 12.3 h |
| v7 | 50.0% | 0.3% | 234 | 19.6 h |
| v7.1 | 22.6% | 2.4% | 228 | 10.0 h |

## Funnel and correction

`1724 stage-one hours → 16221 scoped claims → 4412 screened → 442 investigated → 33 stories`

V7's 4,412→4 election was a policy failure against the pre-declared funnel
shape, not evidence against the signal-scoped unit. V7.1 changes only that
policy: 4412→442
(10.0:1 compression). No
labels entered election or either judge request.

## Runtime and integrity

- Inherited v7 screen invocations: 23 (not rerun)
- New Claude subscription investigation invocations: 23
- Incremental modeled equivalent usage: $17.80 (not API billing)
- OpenAI calls: zero
- Protocol SHA-256: `bd7336d99fc9603c0ecb6913adb4159b64271484e0dfb3025e938c82973040f9`
- Election manifest SHA-256: `759b84ce4c20ace8aeb8d589df13ef50f24306d0f6ae8b926ba019aa5f39bcd8`
- Scorecard SHA-256: `1bba31c415856ff0eedfe6d68efccf36c6436e7e525f9af4d4302fd857083af5`

Both v7 and v7.1 are frozen. This reuses the published v4 test; the full-label
result remains descriptive because the v4 label audit found 30% obvious noise.
