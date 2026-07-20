# Detector v2 calibration and adversarial acceptance

This document is the evidence attached to the frozen alert-capable configuration in
`src/sentinel/config/detector-v2.toml`. The config's full SHA-256 is
`3b11a572823b54f9ea5a0801df2becef809d5e092962ded5bac556b2d1845106`
(`DetectorConfig.version = sha256:3b11a572823b`). No holdout detector output was
inspected while choosing these values.

## Reproduction inputs

- GH Archive: 431/431 continuous hours for `webpack/webpack`, from
  `2025-03-21T00:00:00Z` through the exclusive end
  `2025-04-07T23:00:00Z`.
- npm registry: releases from April 7–8, retained both under the package entity and
  attached to the repository entity as an independent corroboration stream.
- Positive episode: the webpack v5.99.0 issue storm, scored from April 7 12:00–23:00 UTC.
- Predeclared healthy contrast: April 4 12:00 through April 7 12:00 UTC.
- Raw archives and DuckDB files stay in `.cache/`; the code, config, reproduction
  commands, assertions, and derived numbers are committed.

Reproduce without an OpenAI API call:

```bash
uv run python scripts/prepare_detector_v2_data.py webpack --workers 6
uv run python scripts/verify_detector_v2_acceptance.py \
  --db .cache/webpack-v2.duckdb \
  --config src/sentinel/config/detector-v2.toml
```

For a complete per-interval JSON audit:

```bash
uv run python scripts/inspect_detector_v2.py \
  --db .cache/webpack-v2.duckdb \
  --config src/sentinel/config/detector-v2.toml \
  --entity webpack/webpack \
  --start 2025-04-07T12:00:00Z \
  --end 2025-04-07T23:00:00Z
```

## Paired result

The positive episode fires once at `2025-04-07T18:55:02Z`, 79.0 minutes before the
first substantive maintainer acknowledgment at `20:14:04Z`.

| Armed metric | Value | Phase median | Floored MAD | Robust z | Phase samples | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Issues opened / active participant | 0.50 (1 / 2) | 0.00 | 0.10 | 5.0 | 5 | Flagged |
| Comments / active issue thread | 5.50 (11 / 2) | 1.00 | 0.25 | 18.0 | 5 | Flagged; primary |

Raw and bot-excluded series agree exactly in the incident bucket. The only actor
classified as automated anywhere in the hero window is `dependabot[bot]`; no storm
participant is excluded. v5.99.0 is independently visible at `13:12:05Z` in GH Archive
and `13:13:08.298Z` in npm, 63 seconds apart. Those two sources admit the single-bar path;
one release source or multiple ratios derived from one activity stream do not.

The healthy contrast has 197 retained repository events and zero firings. Its interval
diagnostics are 210 `immature`, 76 `insufficient_exposure`, and 2 `quiet`. The automated
actors are `dependabot[bot]` and `linux-foundation-easycla[bot]`, both backed by account
type plus name pattern.

That diagnostic distribution is a real limitation, not a hidden success: webpack is too
quiet for most hourly rate phases to accumulate four non-empty denominator samples. The
incident phase itself has five samples and is valid. We did not lower warmup or exposure
requirements to make sparse healthy hours look mature.

## Threshold provenance

Every alert-capable value below is locked by the positive/healthy pair above and by a
synthetic contract that isolates the intended boundary. The raw pair is the shipping gate:
any change that silences webpack or fires in the healthy contrast is rejected.

| Config boundary | Frozen value | Reproduction evidence |
| --- | ---: | --- |
| History / minimum phase pool | 14 days / 4 samples | Two weeks is the shortest span with four weekend calendar phases; the incident has five usable rate samples and the healthy contrast remains silent. |
| Mild baseline lock | robust z 2.0 | The incident's five clean comparison values survive locking (`locked_samples = 5`); the paired replay stays one firing / zero firings. |
| Persistence | 2 intervals | Synthetic V-shaped single bars remain candidates only; the raw hero uses the independently corroborated release exception. |
| Corroborated-release window | 5 hours | The npm release precedes the 18:00 incident bucket by 4h47m; five is the smallest whole-hour window that admits it. The healthy contrast stays silent. |
| Bot classification | 2 signals; account type + name pattern armed | Raw Dependabot/EasyCLA events match both signals; named human participants match neither. One weak attribute is rejected by contract tests. |
| Issue-rate gates | exposure 2; rate floor 0.50; absolute change 0.25; robust z 3.0; dispersion floors 0.10 absolute / 0.10 fractional | The raw incident clears every bar at 0.50, +0.50, robust z 5.0; the healthy contrast has no admitted candidate. |
| Comment-rate gates | exposure 1; rate floor 2.0; absolute change 1.0; robust z 3.0; dispersion floors 0.25 absolute / 0.10 fractional | The raw incident clears every bar at 5.50, +4.50, robust z 18.0; the healthy contrast has no admitted candidate. |
| Digest routing | attention 0.50 | Webpack's breadth × depth × velocity × confidence is exactly 0.50 and routes to digest. |
| Cooldown | 2 intervals | The paired raw replay surfaces one incident identity; deterministic cooldown/flapping behavior is isolated by tests. |

Two requested series are intentionally not alert-capable yet:

- `first_time_participant_event_share` is computed twice and emits diagnostics, but is
  `observe_only` because no real positive first-observed-participant episode was provided.
- `issue_activity_volume` is a drop-direction gauge on the zero-filled time spine, but is
  `observe_only` because no real repository-activity collapse episode was provided.

Likewise, concentration, repeated-payload, and hyper-regular-timing bot signals are
implemented and contract-tested but not armed in the frozen classifier. Enabling any of
these paths requires its own real bot episode plus a human contrast. Interrupt routing is
also explicitly disabled: no independent raw interrupt-class reproduction exists, so the
code does not pretend that an invented interrupt threshold was calibrated.

## Acceptance contract

`scripts/verify_detector_v2_acceptance.py` fails unless all of these remain true:

1. All 431 disclosed archive hours are complete.
2. The healthy contrast has zero firings.
3. The hero has exactly one firing at `18:55:02Z`, with comments/thread as primary.
4. The firing is before the `20:14:04Z` acknowledgment.
5. v5.99.0 is corroborated by GitHub and npm within 90 seconds.
6. Only Dependabot is excluded in the hero window.
7. Issue #19394 is visible before the firing.

The final GPT attribution fixtures are now committed and checked by the same verifier:
full and no-feedback modes both name `v5.99.0` at 0.99 confidence from inputs ending at
`18:52:11Z`, strictly before the `18:55:02Z` firing. Detector calibration, the holdout,
and ordinary replays make no OpenAI API calls; only these two final successful completions
were seeded live.
