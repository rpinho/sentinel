# Sentinel v7 signal-scoped scorecard

V7 freezes one signal claim per candidate, screens every eligible claim compactly,
fetches heavy evidence only for elected investigations, and publishes deduplicated stories.

## Result

- Published stories: **2**
- Story precision: **50.0%**
- Hour-mapped precision: **50.0%**
- Incident recall: **0.3%**
- True stage-one alert-hours killed: **234**
- Median lead: **19.6 hours**
- Clean-source story precision / incident recall: **50.0% / 0.6%**

| Version | Hour precision | Incident recall | True hours killed | Median lead |
|---|---:|---:|---:|---:|
| v5 | 16.6% | 77.0% | 31 | 11.9 h |
| v6 | 19.6% | 56.4% | 88 | 12.3 h |
| v7 | 50.0% | 0.3% | 234 | 19.6 h |

## Funnel

`1724 stage-one hours → 16221 scoped claims → 4412 screened → 4 investigated → 2 stories`

All 16221 generated candidates have recorded dispositions. Automation dismissed at the candidate gate: 1583.

## Runtime and integrity

- Claude subscription invocations: 23 screen + 1 investigation
- Modeled equivalent usage: $36.89 (not API billing)
- OpenAI calls: zero
- Protocol SHA-256: `dd5cf502cc62108d82792690be0d19a961e85307063c251f7e3c439e0f5f06e9`
- Scorecard SHA-256: `c2097f6ed1039f4295b1d26db2c71cc24472a0a6d98bda68fe68959e0578e19f`

This reuses the published v4 test. The full-label result is descriptive because
the v4 label audit found 30% obvious noise in its comment-derived source; the
clean-source cut is reported separately. No labels entered either judge phase.
